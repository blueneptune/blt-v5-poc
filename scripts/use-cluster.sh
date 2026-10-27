# Point this shell's blt tools at blt on the Kubernetes cluster instead of
# the local podman pod. Source it, don't run it:
#
#   source scripts/use-cluster.sh
#
# Afterwards blt-prefect-block, blt-prefect-import, prefect, collect.sh and
# scripts/job-lookup.sh all talk to https://prefect.<domain> and
# https://api.<domain>. Environment variables win over config/blt.env, so
# no file is changed; open a new shell to go back to the local pod.
#
# Reads the Prefect login and API key from config/blt.env (the cluster
# uses the same ones) without printing them. Trusts the lab's root CA on
# top of the usual public ones.
#
# BLT_CLUSTER_DOMAIN  default blt.lab.ethos.home
# BLT_CA_FILE         the root CA certificate; default: yoki-blt-infra's copy

_blt_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_blt_env="$_blt_root/config/blt.env"
_blt_domain="${BLT_CLUSTER_DOMAIN:-blt.lab.ethos.home}"
_blt_ca="${BLT_CA_FILE:-$_blt_root/../yoki-blt-infra/clusters/core/addons/cert-manager/ethos-home-root-ca.crt}"

_blt_value() {
    local v
    v="$(grep -E "^$1=" "$_blt_env" | tail -1 | cut -d= -f2-)"
    v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
    printf '%s' "$v"
}

if [[ ! -f "$_blt_ca" ]]; then
    echo "use-cluster: root CA not found at $_blt_ca - set BLT_CA_FILE" >&2
elif [[ ! -f "$_blt_env" ]]; then
    echo "use-cluster: $_blt_env not found" >&2
else
    # One bundle: the public CAs Python already trusts, plus the lab's.
    _blt_bundle="${XDG_CACHE_HOME:-$HOME/.cache}/blt/ca-bundle.pem"
    mkdir -p "$(dirname "$_blt_bundle")"
    cat "$(cd "$_blt_root" && uv run --quiet python -c 'import certifi; print(certifi.where())')" \
        "$_blt_ca" >"$_blt_bundle"

    export SSL_CERT_FILE="$_blt_bundle"
    export PREFECT_API_SSL_CERT_FILE="$_blt_bundle"
    export PREFECT_API_URL="https://prefect.$_blt_domain/api"
    export PREFECT_API_AUTH_STRING="$(_blt_value PREFECT_AUTH)"
    export BLT_API_URL="https://api.$_blt_domain"
    export BLT_API_KEY="$(_blt_value BLT_API_KEY)"
    export DO_NOT_TRACK=1
    echo "blt tools now use the cluster: $PREFECT_API_URL, $BLT_API_URL"
fi
unset -f _blt_value
unset _blt_root _blt_env _blt_domain _blt_ca _blt_bundle
