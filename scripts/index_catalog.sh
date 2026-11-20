#!/bin/sh
# Index a catalog into the stage's OpenSearch domain with its in-VPC indexing task.
#
#   ARIA_CONFIG=<toml> sh scripts/index_catalog.sh --catalog <file or s3://...> --version <v> [--replicas N] [--apply]
#
# --catalog   a local JSON array or JSONL file, or s3://<catalog bucket>/catalog/<...>
# --version   the new index is <alias>-<version>: lowercase letters, digits and hyphens; it must not exist
# --replicas  replicas per shard; default the stack's IndexReplicas (0 on single-node Dev, 1 in Prod)
# --apply     write: upload a local file to s3://<bucket>/catalog/<version>/<file>, then run the task
#             with --apply, which builds the index, puts the search pipeline and promotes the alias
#
# Without --apply nothing is written. A local file is validated here, with the stage's catalog
# definition and no AWS calls; an s3:// catalog is validated by the task, which then writes nothing.
# The task's manifest (JSON, counts and names only) is printed on stdout; the script exits with the
# task's exit code. Needs the AWS CLI, with credentials for the stage's account.
set -eu

catalog="" version="" replicas="" apply=""
while [ $# -gt 0 ]; do
    case "$1" in
        --catalog) catalog="${2:?--catalog needs a value}"; shift 2 ;;
        --version) version="${2:?--version needs a value}"; shift 2 ;;
        --replicas) replicas="${2:?--replicas needs a value}"; shift 2 ;;
        --apply) apply=yes; shift ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "Unknown argument: $1 (see --help)" >&2; exit 2 ;;
    esac
done
: "${ARIA_CONFIG:?Set the deployment configuration path}"
export ARIA_CONFIG="$(realpath "$ARIA_CONFIG")"
[ -n "$catalog" ] && [ -n "$version" ] || { echo "--catalog and --version are required (see --help)" >&2; exit 2; }
printf '%s' "$version" | grep -Eq '^[a-z0-9][a-z0-9-]{0,62}$' \
    || { echo "--version must be lowercase letters, digits and hyphens" >&2; exit 2; }
case "$replicas" in ''|0|1|2) ;; *) echo "--replicas must be 0, 1 or 2" >&2; exit 2 ;; esac
case "$catalog" in
    s3://*) remote=yes ;;
    *) remote=""
       [ -f "$catalog" ] || { echo "--catalog $catalog is not a file" >&2; exit 2; }
       catalog="$(realpath "$catalog")" ;;
esac
cd "$(dirname "$0")/.."

log() { echo "index_catalog: $*" >&2; }

# The stage, from the same loader that synthesizes it: its namespace, Region, provider and definition.
stage=$(uv run --project packages/infra --frozen python - <<'PY'
import shlex, sys
sys.path.insert(0, "packages/infra")
from config.loader import load_config
config = load_config()
search = config.tools.product_search
for name, value in (("ns", config.ns), ("region", config.app.region), ("provider", search.provider),
                    ("alias", search.open_search_index), ("definition", search.definition_json)):
    print(f"{name}={shlex.quote(value)}")
PY
)
eval "$stage"
[ "$provider" = opensearch ] || { echo "tools.productSearch.provider is $provider; the indexing task needs opensearch" >&2; exit 2; }
region="${region:-${AWS_REGION:-${AWS_DEFAULT_REGION:-}}}"
[ -n "$region" ] || { echo "Set app.region in $ARIA_CONFIG, or AWS_REGION" >&2; exit 2; }

if [ -z "$remote" ] && [ -z "$apply" ]; then
    # A local dry run: the indexer itself, here, with no AWS calls.
    log "dry run: validating $catalog locally with the stage's catalog definition"
    CATALOG_DEFINITION="$definition" PYTHONPATH=packages/indexer/src \
        uv run --project packages/indexer --frozen python -m aria_indexer \
        --catalog "$catalog" --version "$version" --alias "$alias" ${replicas:+--replicas "$replicas"}
    exit $?
fi

# The indexing task's settings, from the Opensearch stack's outputs.
outputs=$(aws cloudformation describe-stacks --region "$region" --stack-name "${ns}Opensearch" \
    --query 'Stacks[0].Outputs' --output json)
settings=$(printf '%s' "$outputs" | python3 -c '
import json, shlex, sys
outputs = {o["OutputKey"]: o["OutputValue"] for o in json.load(sys.stdin)}
names = {"bucket": "CatalogBucketName", "cluster": "IndexerCluster", "task_definition": "IndexerTaskDefinitionArn",
         "subnets": "IndexerSubnets", "security_group": "IndexerSecurityGroup", "log_group": "IndexerLogGroup",
         "deployed_alias": "ProductAlias", "default_replicas": "IndexReplicas", "domain": "DomainName",
         "nori_package": "NoriPackageId"}
missing = [key for key in names.values() if key not in outputs and key != "NoriPackageId"]
if missing:
    sys.exit(f"The stack has no outputs {missing}; deploy this version first")
for name, key in names.items():
    print(name + "=" + shlex.quote(outputs.get(key) or ""))
')
eval "$settings"
[ "$deployed_alias" = "$alias" ] || { echo "The stack searches $deployed_alias, the config $alias; deploy the config first" >&2; exit 2; }
replicas="${replicas:-$default_replicas}"

if [ -n "$apply" ] && [ -n "$nori_package" ]; then
    # The definition analyzes text with Nori: its association, which finishes after the deploy, must be done.
    status=$(aws opensearch list-packages-for-domain --region "$region" --domain-name "$domain" \
        --query "DomainPackageDetailsList[?PackageID=='$nori_package'].DomainPackageStatus | [0]" --output text)
    case "$status" in
        ACTIVE) ;;
        None|'') echo "The Nori plugin ($nori_package) isn't associated with $domain; redeploy the Opensearch stack" >&2; exit 1 ;;
        *) echo "The Nori plugin is $status on $domain, not ACTIVE yet; wait and retry" >&2; exit 1 ;;
    esac
fi

if [ -n "$remote" ]; then
    case "$catalog" in
        "s3://$bucket/catalog/"?*) uri="$catalog" ;;
        *) echo "An s3:// catalog must be under s3://$bucket/catalog/, the only prefix the task can read" >&2; exit 2 ;;
    esac
else
    uri="s3://$bucket/catalog/$version/$(basename "$catalog")"
    log "uploading $catalog to $uri"
    aws s3 cp --region "$region" --only-show-errors "$catalog" "$uri"
fi

overrides=$(python3 -c '
import json, sys
uri, version, alias, replicas, apply = sys.argv[1:]
command = ["--catalog", uri, "--version", version, "--alias", alias, "--replicas", replicas] + (["--apply"] if apply else [])
print(json.dumps({"containerOverrides": [{"name": "indexer", "command": command}]}))
' "$uri" "$version" "$alias" "$replicas" "$apply")

log "starting the indexing task ($([ -n "$apply" ] && echo apply || echo dry run)) in $cluster"
task=$(aws ecs run-task --region "$region" --cluster "$cluster" --task-definition "$task_definition" \
    --launch-type FARGATE --count 1 --started-by aria-index-catalog \
    --network-configuration "awsvpcConfiguration={subnets=[$subnets],securityGroups=[$security_group],assignPublicIp=DISABLED}" \
    --overrides "$overrides" --query 'tasks[0].taskArn' --output text)
case "$task" in arn:*) ;; *) echo "The task didn't start; see aws ecs run-task's failures" >&2; exit 1 ;; esac
log "task $task"

# The waiter gives up after 10 minutes; a large catalog takes longer, so wait until it has stopped.
until aws ecs wait tasks-stopped --region "$region" --cluster "$cluster" --tasks "$task" 2>/dev/null; do
    state=$(aws ecs describe-tasks --region "$region" --cluster "$cluster" --tasks "$task" \
        --query 'tasks[0].lastStatus' --output text)
    [ "$state" = STOPPED ] && break
    log "still $state"
done
result=$(aws ecs describe-tasks --region "$region" --cluster "$cluster" --tasks "$task" \
    --query 'tasks[0].[containers[?name==`indexer`].exitCode | [0], stoppedReason]' --output text)
exit_code=$(printf '%s' "$result" | cut -f1)
case "$exit_code" in ''|None) log "the indexer didn't run: $(printf '%s' "$result" | cut -f2-)"; exit_code=1 ;; esac

# The manifest is the task's last JSON line with a "manifest" key; logs can lag the task's stop.
stream="indexer/indexer/${task##*/}"
attempt=0
while :; do
    events=$(aws logs get-log-events --region "$region" --log-group-name "$log_group" --log-stream-name "$stream" \
        --limit 200 --output json 2>/dev/null || echo '{"events": []}')
    if printf '%s' "$events" | python3 -c '
import json, sys
messages = [event["message"] for event in json.load(sys.stdin)["events"]]
manifests = []
for message in messages:
    try:
        line = json.loads(message)
    except ValueError:
        continue
    if isinstance(line, dict) and "manifest" in line:
        manifests.append(message)
if not manifests:
    sys.exit(1)
for message in messages:
    if message not in manifests:
        print(message, file=sys.stderr)
print(manifests[-1])
'; then
        break
    fi
    attempt=$((attempt + 1))
    [ "$attempt" -lt 5 ] || { log "no manifest in $log_group/$stream"; break; }
    sleep 3
done
exit "$exit_code"
