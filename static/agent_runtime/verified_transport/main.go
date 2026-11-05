// The supervisor is the only caller. There is no listening socket or proxy API.
package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"os"
	"time"

	"github.com/tinfoilsh/tinfoil-go/verifier"
	"github.com/tinfoilsh/tinfoil-go/verifier/client"
	"github.com/tinfoilsh/tinfoil-go/verifier/document"
)

const (
	host       = "deepseek-v4-1-flash-inf16-0.tinfoil.containers.tinfoil.dev"
	repo       = "tinfoilsh/confidential-deepseek-v4-1-flash"
	digest     = "71f41a053232a06dd6ed61ca3a40502a9b972dc16dd9f04ca895ced5a7809def"
	key        = "bd4fd6b24ad43d34041312694cd9bc6cdf83380829328eb83adf0442601c5488"
	model      = "deepseek-v4-1-flash"
	frameLimit = 2_097_152
)

var denied = errors.New("verified_transport_failed")

type frame struct {
	Op         string          `json:"op"`
	Host       string          `json:"host,omitempty"`
	Repo       string          `json:"repo,omitempty"`
	Digest     string          `json:"digest,omitempty"`
	Key        string          `json:"key,omitempty"`
	Credential string          `json:"credential,omitempty"`
	Path       string          `json:"path,omitempty"`
	Body       json.RawMessage `json:"body,omitempty"`
}

type reply struct {
	OK           bool                  `json:"ok"`
	Error        string                `json:"error,omitempty"`
	Status       int                   `json:"status,omitempty"`
	Body         json.RawMessage       `json:"body,omitempty"`
	Verification *verificationDocument `json:"verification,omitempty"`
}

type verificationDocument struct {
	SecurityVerified   bool                         `json:"securityVerified"`
	ConfigRepo         string                       `json:"configRepo"`
	EnclaveHost        string                       `json:"enclaveHost"`
	ReleaseDigest      string                       `json:"releaseDigest"`
	TLSPublicKey       string                       `json:"tlsPublicKey"`
	Verifier           map[string]string            `json:"verifier"`
	VerifiedAt         string                       `json:"verifiedAt"`
	FreshnessExpiresAt time.Time                    `json:"freshnessExpiresAt"`
	Steps              map[string]map[string]string `json:"steps"`
	CodeTag            string                       `json:"codeTag"`
	AttestationSHA256  string                       `json:"attestationSHA256"`
	PlatformReference  map[string]string            `json:"platformReference"`
}

type verifierSource interface {
	Verify() (*verificationDocument, error)
	HTTPClient() (*http.Client, error)
}

type sdkVerifier struct{}

func (sdkVerifier) Verify() (*verificationDocument, error) {
	nonce, err := document.RandomNonce()
	if err != nil {
		return nil, err
	}
	raw, err := document.FetchVia(host, "", nonce)
	if err != nil {
		return nil, err
	}
	core, err := verifier.New()
	if err != nil {
		return nil, err
	}
	// Exact repository/digest is an input to provenance verification, not learned
	// from the remote host. Official vendor roots/platform policy stay untouched.
	verified, err := core.VerifyV3(raw, nonce, repo+"@sha256:"+digest)
	if err != nil {
		return nil, err
	}
	fp, err := verified.TLSPublicKeyFP()
	if err != nil {
		return nil, err
	}
	parsed, err := document.Parse(raw, nonce)
	if err != nil {
		return nil, err
	}
	platform, err := parsed.ReferenceValuesCollateral(document.CollateralSigstorePlatformV1Format)
	if err != nil {
		return nil, err
	}
	d := &verificationDocument{
		SecurityVerified: true, ConfigRepo: repo, EnclaveHost: host, ReleaseDigest: verified.CodeDigest,
		TLSPublicKey: fp, Verifier: map[string]string{"name": "tinfoil-go", "version": client.Version},
		VerifiedAt: time.Now().UTC().Format(time.RFC3339Nano), FreshnessExpiresAt: verified.FreshnessExpiresAt,
		Steps:   map[string]map[string]string{},
		CodeTag: verified.CodeTag, AttestationSHA256: fmt.Sprintf("%x", sha256.Sum256(raw)),
		PlatformReference: map[string]string{"repo": platform.Repo, "tag": platform.Tag, "digest": platform.Digest},
	}
	for _, name := range []string{"fetchDigest", "verifyCode", "verifyEnclave", "compareMeasurements"} {
		d.Steps[name] = map[string]string{"status": "success"}
	}
	return d, nil
}
func (sdkVerifier) HTTPClient() (*http.Client, error) {
	// Avoid the SDK client wrapper's refresh/recovery. Only the exact reviewed key
	// authenticated above can open a TLS connection carrying a sensitive body.
	return &http.Client{Transport: &client.TLSBoundRoundTripper{ExpectedPublicKey: key}}, nil
}

type broker struct {
	secure     verifierSource
	credential string
	document   *verificationDocument
}

// SDK Verify authenticates Sigstore release provenance, platform endorsements,
// enclave measurements and endorsed TLS key. Only then do our immutable pins pass.
func check(d *verificationDocument) error {
	if d == nil || !d.SecurityVerified || d.ConfigRepo != repo || d.EnclaveHost != host ||
		d.ReleaseDigest != digest || d.TLSPublicKey != key || !time.Now().Before(d.FreshnessExpiresAt) {
		return denied
	}
	for _, name := range []string{"fetchDigest", "verifyCode", "verifyEnclave", "compareMeasurements"} {
		if d.Steps[name]["status"] != "success" {
			return denied
		}
	}
	return nil
}

func (b *broker) verify() error {
	d, err := b.secure.Verify()
	if err != nil {
		return denied
	}
	if err := check(d); err != nil {
		return err
	}
	b.document = d
	return nil
}

func validBody(path string, body []byte) bool {
	var p map[string]json.RawMessage
	if json.Unmarshal(body, &p) != nil {
		return false
	}
	var id string
	if json.Unmarshal(p["model"], &id) != nil || id != model {
		return false
	}
	if path == "/tokenize" {
		return true
	}
	if path != "/v1/chat/completions" {
		return false
	}
	var stream bool
	var tokens int
	return json.Unmarshal(p["stream"], &stream) == nil && !stream &&
		json.Unmarshal(p["max_tokens"], &tokens) == nil && tokens > 0 && tokens <= 1_048_576
}

func (b *broker) request(f frame) (reply, error) {
	if !validBody(f.Path, f.Body) {
		return reply{}, denied
	}
	// A fresh verification and exact pin check BEFORE attaching credentials/body.
	if err := b.verify(); err != nil {
		return reply{}, err
	}
	hc, err := b.secure.HTTPClient()
	if err != nil {
		return reply{}, denied
	}
	defer hc.CloseIdleConnections()
	hc.Timeout = 120 * time.Second
	hc.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	deadline := time.Now().Add(120 * time.Second)
	if b.document.FreshnessExpiresAt.Before(deadline) {
		deadline = b.document.FreshnessExpiresAt
	}
	ctx, cancel := context.WithDeadline(context.Background(), deadline)
	defer cancel()
	// Non-rewindable POST: no transparent net/http replay on a stale connection.
	req, err := http.NewRequestWithContext(ctx, "POST", "https://"+host+f.Path, io.NopCloser(bytes.NewReader(f.Body)))
	if err != nil {
		return reply{}, denied
	}
	req.ContentLength = int64(len(f.Body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+b.credential)
	resp, err := hc.Do(req)
	if err != nil {
		return reply{}, denied
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 && resp.StatusCode < 400 {
		return reply{}, denied
	}
	// Never propagate an upstream auth error body (it can echo credentials).
	if resp.StatusCode == 401 || resp.StatusCode == 403 {
		return reply{OK: true, Status: resp.StatusCode, Body: json.RawMessage(`{}`), Verification: b.document}, nil
	}
	mediaType, _, err := mime.ParseMediaType(resp.Header.Get("Content-Type"))
	if err != nil || mediaType != "application/json" {
		return reply{}, denied
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, frameLimit+1))
	if err != nil || len(body) > frameLimit || !json.Valid(body) {
		return reply{}, denied
	}
	var object map[string]json.RawMessage
	if json.Unmarshal(body, &object) != nil || object == nil {
		return reply{}, denied
	}
	return reply{OK: true, Status: resp.StatusCode, Body: body, Verification: b.document}, nil
}

func decodeFrame(raw []byte) (frame, error) {
	var f frame
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(&f); err != nil {
		return f, denied
	}
	if d.Decode(new(any)) != io.EOF {
		return f, denied
	}
	return f, nil
}

func run(in io.Reader, out io.Writer) {
	scan := bufio.NewScanner(in)
	scan.Buffer(make([]byte, 4096), frameLimit)
	enc := json.NewEncoder(out)
	fail := func() { _ = enc.Encode(reply{Error: "verified_transport_failed"}) }
	if !scan.Scan() {
		fail()
		return
	}
	f, err := decodeFrame(scan.Bytes())
	if err != nil || f.Op != "init" || f.Host != host || f.Repo != repo || f.Digest != digest || f.Key != key || f.Credential == "" || len(f.Credential) > 4096 {
		fail()
		return
	}
	b := broker{secure: sdkVerifier{}, credential: f.Credential}
	if b.verify() != nil {
		fail()
		return
	}
	if enc.Encode(reply{OK: true, Verification: b.document}) != nil {
		return
	}
	for scan.Scan() {
		f, err := decodeFrame(scan.Bytes())
		if err != nil || f.Op != "request" || f.Credential != "" || f.Host != "" || f.Repo != "" || f.Digest != "" || f.Key != "" {
			fail()
			return
		}
		r, err := b.request(f)
		if err != nil {
			fail()
			return
		}
		encoded, err := json.Marshal(r)
		if err != nil || len(encoded) >= frameLimit {
			fail()
			return
		}
		if _, err = out.Write(append(encoded, '\n')); err != nil {
			return
		}
	}
	if scan.Err() != nil {
		fail()
	}
}

func main() {
	run(os.Stdin, os.Stdout)
}
