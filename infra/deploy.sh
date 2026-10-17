#!/bin/sh
# Publishes the API, the PWA and the landing page (at /landing/) to the resources Terraform created. Run from
# infra/ after `terraform apply` (needs `az login`, Azure Functions Core Tools and Node.js).
#   ./deploy.sh        everything
#   ./deploy.sh web    only the PWA and the landing page (the Static Web App is replaced as a whole)
set -eu
cd "$(dirname "$0")"

app=$(terraform output -raw function_app)
api_url=$(terraform output -raw api_url)
swa_token=$(terraform output -raw static_web_app_deployment_token)

if [ "${1:-}" != "web" ]; then
  echo "Publishing the API to $app"
  (cd ../backend && func azure functionapp publish "$app" --python)
fi

# The PWA is deployed from a copy whose config.js points at the Function App.
dist=$(mktemp -d)
trap 'rm -rf "$dist"' EXIT
cp -R ../frontend/. "$dist"
rm -rf "$dist/dev-server.js" "$dist/scripts"
printf "window.GUSANITO = { apiBase: '%s' };\n" "$api_url" > "$dist/config.js"
# The landing page shares the PWA's origin: it uses its vendor/ scripts and offline map.
cp -R ../landing "$dist/landing"
[ -f "$dist/zambia.pmtiles" ] || echo "warning: frontend/zambia.pmtiles is missing, the offline map will be blank (run frontend/scripts/make-tiles.sh)"

echo "Publishing the PWA"
# The token goes in the environment: npx echoes its command line, flags included.
SWA_CLI_DEPLOYMENT_TOKEN="$swa_token" npx --yes @azure/static-web-apps-cli deploy "$dist" --env production
echo "PWA: $(terraform output -raw pwa_url)"
echo "Landing: $(terraform output -raw pwa_url)/landing/"
