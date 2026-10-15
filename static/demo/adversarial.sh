#!/usr/bin/env bash
# Operator-assisted probes. Provisioning remains the operator's explicit action.
set +x
set -euo pipefail

usage() {
  printf '%s\n' \
    'Usage: bash demo/adversarial.sh [--stage all|tamper|debug|override|bypass|policy|restart]' \
    '       [--execute --host HOST --repo OWNER/REPO --expected-digest HEX64' \
    '        --expected-policy-digest HEX64 [--candidate-release-digest HEX64]' \
    '        [--inference-url ORIGIN] [--state-file PATH]]' \
    'Default: dry-run; no commands, credentials, model, or deployment required.' \
    '--execute requires one stage and interactive setup/restoration pauses.' \
    'override executes only the local startup configuration guard and needs no target.' \
    'policy requires an independently approved candidate release digest.' \
    'bypass requires an independently identified direct inference origin.' \
    'restart requires a new checkpoint path; the checkpoint is retained.' \
    'Never pass secrets as arguments. This script never loads .env or publishes a release.'
}
fail() { printf '%s\n' "ERROR: $1" >&2; exit 1; }
stage=all execute=false host= repo= digest= policy= candidate= inference= state_file=
declare -A seen=()
while (($#)); do
  option=$1
  [[ ! ${seen[$option]+present} ]] || fail 'Duplicate option.'
  seen[$option]=1
  case "$option" in
    --help|-h) usage; exit 0 ;;
    --execute) execute=true; shift ;;
    --stage|--host|--repo|--expected-digest|--expected-policy-digest|--candidate-release-digest|--inference-url|--state-file)
      (($# >= 2)) && [[ -n $2 && $2 != --* ]] || fail 'Option requires a value.'
      case "$option" in
        --stage) stage=$2 ;; --host) host=$2 ;; --repo) repo=$2 ;;
        --expected-digest) digest=$2 ;; --expected-policy-digest) policy=$2 ;;
        --candidate-release-digest) candidate=$2 ;; --inference-url) inference=$2 ;;
        --state-file) state_file=$2 ;;
      esac
      shift 2 ;;
    *) fail 'Unknown option.' ;;
  esac
done
case "$stage" in all|tamper|debug|override|bypass|policy|restart) ;; *) fail 'Unknown stage.' ;; esac

narrate() {
  case "$1" in
    tamper)
      printf '%s\n' '1. TAMPER: In a disposable release checkout, change one byte in classifiers/ai_capabilities.md; rebuild and redeploy through the reviewed release procedure.' \
        'Keep the independently approved original release and policy pins. Expected: Release digest does not match independently approved digest.' ;;
    debug)
      printf '%s\n' '2. DEBUG: Provision a disposable debug enclave using the platform-supported procedure. Do not guess a debug CLI flag.' \
        'Expected: verifier rejection. A generic CLI failure alone cannot establish that debug policy caused rejection; confirm hardware-debug evidence independently.' ;;
    override)
      printf '%s\n' '3. OVERRIDE: Set CLASSIFIER_ENDPOINT_OVERRIDE=http://unattested.invalid in an isolated startup-configuration probe.' \
        'Expected startup error: CLASSIFIER_ENDPOINT_OVERRIDE is unsupported. No endpoint is contacted; the guard precedes secret/model loading.' ;;
    bypass)
      printf '%s\n' '4. BYPASS: Quiesce proxy traffic; wait for all admitted work to finalize. Verify and record the proxy rollup, then send 3 synthetic requests directly to inference.' \
        'Compare the independent generator count with the change in requests_total in finalized proxy reports, using the same boot and retained history.' \
        'The auditor alone cannot detect bypass. Independent generator counts supply demo ground truth only; trusted coverage remains deferred.' ;;
    policy)
      printf '%s\n' '5. POLICY: Change only BLOCK_THRESHOLD, retain the exact OCI image, and restart. Keep the original approved policy digest.' \
        'Measured YAML changes can also change the Tinfoil release digest: independently approve that candidate release, without approving its changed policy.' \
        'Expected: Policy digest does not match independently approved digest, despite the same OCI image. Never substitute an observed digest for independent approval.' ;;
    restart)
      printf '%s\n' '6. RESTART: Save a fresh auditor --state-file checkpoint after a finalized period; restart with the same approved release/settings; wait for a new finalized report.' \
        'Expected: Boot changed since checkpoint; continuity is unknown. The checkpoint does not recover lost reports or detect unseen forks/restarts.' ;;
  esac
  if [[ $1 != override ]]; then
    printf '%s\n' 'Restore the original approved source, release/config/settings and non-debug deployment after this stage; verify with the original release and policy pins before continuing.'
  fi
}

printf '%s\n' 'AIAB adversarial demo: synthetic traffic only; endpoint evidence does not establish datacenter coverage or adversarial classifier robustness.'
if ! "$execute"; then
  printf '%s\n' 'DRY RUN: no commands executed. Release/org/hostname/approved digests remain operator-supplied; see deploy/tinfoil/README.md.'
  for item in tamper debug override bypass policy restart; do
    if [[ $stage == all || $stage == "$item" ]]; then narrate "$item"; fi
  done
  printf '%s\n' \
    'Audit probe: uv run --locked auditor verify --host HOSTNAME --repo OWNER/REPO --expected-digest APPROVED_TINFOIL_RELEASE_DIGEST --expected-policy-digest APPROVED_POLICY_DIGEST' \
    'Continuity probe: uv run --locked auditor rollup --host HOSTNAME --repo OWNER/REPO --expected-digest APPROVED_TINFOIL_RELEASE_DIGEST --expected-policy-digest APPROVED_POLICY_DIGEST --state-file PATH' \
    'Run one --stage with --execute to perform probes. Deployment changes are manual, with explicit pauses; this script does not provision or publish.'
  exit 0
fi
[[ $stage != all ]] || fail '--execute requires one explicit stage.'
command -v uv >/dev/null || fail 'uv is required; install locked dependencies first.'
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd -- "$script_dir/.."
narrate "$stage"
if [[ $stage == override ]]; then
  uv run --locked python -c 'from aiab.config import Config, ConfigurationError
try:
    Config.from_env({"CLASSIFIER_ENDPOINT_OVERRIDE": "http://unattested.invalid"})
except ConfigurationError as error:
    if str(error) != "CLASSIFIER_ENDPOINT_OVERRIDE is unsupported":
        raise SystemExit("Unexpected startup rejection") from None
    print("DETECTED: CLASSIFIER_ENDPOINT_OVERRIDE is unsupported (startup configuration refused)")
else:
    raise SystemExit("FAIL: unsupported override was accepted")'
  exit 0
fi
[[ -n $host && -n $repo && -n $digest && -n $policy ]] || fail 'Target, repository, release digest and policy digest are required.'
[[ $digest =~ ^[0-9a-f]{64}$ && $policy =~ ^[0-9a-f]{64}$ ]] || fail 'Digests must be 64 lowercase hex digits.'
[[ $host =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ && $repo =~ ^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$ ]] || fail 'Invalid public host or owner/repo.'
[[ $stage != policy || $candidate =~ ^[0-9a-f]{64}$ ]] || fail 'Policy stage requires an independently approved candidate release digest.'
[[ $stage != bypass || -n $inference ]] || fail 'Bypass stage requires a direct inference origin.'
[[ $stage != restart || ( -n $state_file && ! -e $state_file && ! -L $state_file ) ]] || fail 'Restart stage requires a new checkpoint path.'
[[ -t 0 ]] || fail 'Deployment probes require interactive operator setup/restoration pauses.'
command -v tinfoil >/dev/null || fail 'Reviewed tinfoil CLI must be installed.'
trap 'printf "%s\n" "Before another scenario, restore approved non-debug release/settings and verify original pins. Any restart checkpoint is retained." >&2' EXIT
pause() {
  local answer
  printf '%s\n' "$1" 'Type ready after completing these steps (anything else aborts):'
  IFS= read -r answer
  [[ $answer == ready ]] || fail 'Operator aborted.'
}
audit() {
  local command=$1 release=$2
  shift 2
  uv run --locked auditor "$command" --host "$host" --repo "$repo" \
    --expected-digest "$release" --expected-policy-digest "$policy" "$@"
}
verify_original() {
  if ! output=$(audit verify "$digest" 2>&1); then fail 'Approved baseline verification failed; raw output suppressed.'; fi
  printf '%s\n' 'VERIFIED: original approved release and policy.'
}
expect_rejection() {
  local release=$1 expected=$2
  if output=$(audit verify "$release" 2>&1); then fail 'Unexpected verification success.'; fi
  [[ $output == *"$expected"* ]] || fail 'Verification failed for another reason; scenario is INCONCLUSIVE. Raw output suppressed.'
  printf 'DETECTED: %s\n' "$expected"
}
rollup() {
  if ! output=$(audit rollup "$digest" "$@" 2>&1); then fail 'Rollup verification failed; raw output suppressed.'; fi
  local pattern='Totals \(([0-9]+) unique periods; boot ([0-9a-f-]+)\): [0-9]+ selected of ([0-9]+) admitted'
  [[ $output =~ $pattern ]] || fail 'Unsupported auditor totals output.'
  periods=${BASH_REMATCH[1]} boot=${BASH_REMATCH[2]} total=${BASH_REMATCH[3]}
  ((periods > 0)) || fail 'No finalized reports; wait for period end plus 30-second processing allowance.'
  printf 'VERIFIED ROLLUP: %s finalized periods; requests_total=%s\n' "$periods" "$total"
}

pause 'Prepare the original approved non-debug deployment and independently approved pins. No mutation should be active yet.'
verify_original
case "$stage" in
  tamper)
    pause 'Now rebuild/redeploy the one-byte classifier mutation in the disposable release. Preserve original expected pins.'
    expect_rejection "$digest" 'Release digest does not match independently approved digest' ;;
  debug)
    pause 'Now provision the debug enclave and independently confirm debug hardware evidence. Keep the audit target reachable.'
    if output=$(audit verify "$digest" 2>&1); then fail 'Unexpected verification success for debug target.'; fi
    [[ $output == *'Tinfoil attestation command rejected the enclave'* || $output == *'Incomplete, debug, or unsuccessful Tinfoil verification record'* ]] || fail 'Different verification failure; debug scenario is INCONCLUSIVE.'
    printf '%s\n' 'REJECTED: verifier refused this target. Debug causality requires the independent evidence checked above; generic CLI failure can also mean an outage.' ;;
  policy)
    pause 'Now activate the reviewed candidate release with changed BLOCK_THRESHOLD and the same OCI image. Preserve the original policy pin.'
    expect_rejection "$candidate" 'Policy digest does not match independently approved digest' ;;
  bypass)
    pause 'Quiesce proxy traffic and wait for all pending work to finalize. Confirm the direct inference origin is outside the proxy.'
    rollup
    before_total=$total before_boot=$boot before_periods=$periods
    before_rollup=$output
    if ! traffic=$(uv run --locked python -m traffic.generate --url "$inference" --count 3 2>&1); then fail 'Direct generator failed; bypass is INCONCLUSIVE. Raw output suppressed.'; fi
    [[ $traffic == *'Successes: 3;'* ]] || fail 'Not all three direct requests succeeded; bypass is INCONCLUSIVE.'
    printf '%s\n' 'INDEPENDENT GROUND TRUTH: 3 direct inference requests succeeded.'
    pause 'Wait at least the configured report period plus 30 seconds after direct traffic, with no other proxy traffic. Do not restart or exhaust report retention.'
    rollup
    [[ $boot == "$before_boot" ]] && ((periods > before_periods && total >= before_total)) || fail 'Boot/history changed or no new finalized period; comparison is INCONCLUSIVE.'
    while IFS= read -r line; do
      if [[ $line =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2} ]]; then
        [[ $output == *"$line"* ]] || fail 'A baseline period was evicted or changed; bypass comparison is INCONCLUSIVE.'
      fi
    done <<< "$before_rollup"
    printf 'Observed requests_total change at proxy: %s; independent direct successes: 3.\n' "$((total-before_total))"
    ((total == before_total)) || fail 'Proxy counts changed; quiescence or routing assumption failed. Do not claim a clean bypass result.'
    printf '%s\n' 'LIMIT: proxy reports omit these 3 direct requests. The auditor alone cannot detect bypass; this comparison relies on independent generator ground truth and controlled retention.' ;;
  restart)
    pause 'Wait for a finalized period before saving the new local checkpoint.'
    rollup --state-file "$state_file"
    printf '%s\n' 'CHECKPOINT SAVED: first use means earlier continuity is unknown.'
    pause 'Restart with exactly the original approved release/settings. Wait for a new finalized report (period end plus 30 seconds).'
    rollup --state-file "$state_file"
    [[ $output == *'Boot changed since checkpoint; continuity is unknown'* ]] || fail 'Expected new-boot warning absent.'
    printf '%s\n' 'WARNING: Boot changed since checkpoint; continuity is unknown.' \
      'LIMIT: the checkpoint cannot recover lost reports, prove completeness, or detect unseen forks/restarts. This is incomplete evidence, not proof of malicious behavior.' ;;
esac
pause 'Restore original approved source, release/config/settings and non-debug deployment. For bypass/restart confirm approved settings remain active.'
verify_original
printf '%s\n' 'Stage complete; approved settings verified. Run the next stage explicitly.'
