#!/bin/sh
# Publishes the API and the PWA to the resources Terraform created. Run from infra/ after `terraform apply`
# (needs `az login`, Azure Functions Core Tools and Node.js).
set -eu
cd "$(dirname "$0")"

app=$(terraform output -raw function_app)
api_url=$(terraform output -raw api_url)
swa_token=$(terraform output -raw static_web_app_deployment_token)

echo "Publishing the API to $app"
(cd ../backend && func azure functionapp publish "$app" --python)

# The PWA is deployed from a copy whose config.js points at the Function App.
dist=$(mktemp -d)
trap 'rm -rf "$dist"' EXIT
cp -R ../frontend/. "$dist"
cp -R ../landing "$dist/landing" # the demo page, at /landing/
rm -rf "$dist/dev-server.js" "$dist/scripts"
printf "window.GUSANITO = { apiBase: '%s' };\n" "$api_url" > "$dist/config.js"
[ -f "$dist/zambia.pmtiles" ] || echo "warning: frontend/zambia.pmtiles is missing, the offline map will be blank (run frontend/scripts/make-tiles.sh)"

echo "Publishing the PWA"
npx --yes @azure/static-web-apps-cli deploy "$dist" --deployment-token "$swa_token" --env production
echo "PWA: $(terraform output -raw pwa_url)"
