#!/bin/sh
set -eu

# Accept an HTTP(S) origin with an optional unencoded ASCII path prefix.
# Keep this grammar aligned with configuredBaseUrl() in index.html. Reject all
# percent encodings so neither browsers nor proxies can decode a different path.
# Never echo invalid input: deployment configuration may contain credentials.
if ! printf '%s\n' "${AIAB_URL-}" | awk '
    function ipv6(address, halves, pieces, n, groups, j, k) {
        n = split(address, halves, "::")
        if (n > 2) return 0
        groups = 0
        for (j = 1; j <= n; j++) {
            if (halves[j] == "" && n == 2) continue
            k = split(halves[j], pieces, ":")
            for (p = 1; p <= k; p++) {
                if (pieces[p] !~ /^[0-9a-fA-F]+$/ || length(pieces[p]) > 4) return 0
            }
            groups += k
        }
        return n == 2 ? groups < 8 : groups == 8
    }
    NR != 1 { bad = 1 }
    NR == 1 {
        value = $0
        if (value !~ /^https?:\/\//) { bad = 1; next }
        sub(/^https?:\/\//, "", value)
        path = value
        sub(/^[^\/]*/, "", path)
        value = substr(value, 1, length(value) - length(path))
        if (path != "" && path != "/") {
            if (path !~ /^\/[A-Za-z0-9._~-]+(\/[A-Za-z0-9._~-]+)*\/?$/) bad = 1
            count = split(path, segments, "/")
            for (i = 1; i <= count; i++) {
                if (segments[i] == "." || segments[i] == "..") bad = 1
            }
        }
        if (value ~ /^\[[0-9a-fA-F:]+\](:[0-9]+)?$/) {
            address = value
            sub(/^\[/, "", address)
            sub(/\].*$/, "", address)
            if (!ipv6(address)) bad = 1
            sub(/^\[[0-9a-fA-F:]+\]/, "", value)
        } else {
            host = value
            sub(/:[0-9]+$/, "", host)
            count = split(host, labels, ".")
            if (count < 1 || host == "" || length(host) > 253) bad = 1
            for (i = 1; i <= count; i++) {
                if (labels[i] !~ /^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$/ ||
                    length(labels[i]) > 63) bad = 1
            }
            if (host ~ /^[0-9.]+$/) {
                if (count != 4) bad = 1
                for (i = 1; i <= count; i++) {
                    if (labels[i] + 0 > 255 ||
                        (length(labels[i]) > 1 && substr(labels[i], 1, 1) == "0")) bad = 1
                }
            } else if (labels[count] ~ /^[0-9]+$/ ||
                       labels[count] ~ /^0[xX][0-9a-fA-F]+$/) {
                # WHATWG URLs interpret numeric final labels as IPv4; do not
                # accept alternate hex, octal, or abbreviated IP spellings.
                bad = 1
            }
            value = substr(value, length(host) + 1)
        }
        if (value != "" && (value !~ /^:[0-9]+$/ ||
            substr(value, 2) + 0 < 1 || substr(value, 2) + 0 > 65535)) bad = 1
    }
    END { exit (NR != 1 || bad) }
'; then
    printf '%s\n' 'AIAB_URL must be a valid HTTP(S) origin with an optional safe path prefix.' >&2
    exit 1
fi

# The allowlist above guarantees JSON/JavaScript string safety. printf treats the
# URL as data, never a format string. Write before nginx can serve any content.
printf 'window.AIAB_URL = "%s";\n' "${AIAB_URL%/}" > "${WEBCHAT_ROOT:-/usr/share/nginx/html}/config.js"
exec nginx -g 'daemon off;'
