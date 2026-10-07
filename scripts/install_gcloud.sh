#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
sdk_dir="$PWD/.cache/google-cloud-sdk-install"
sdk_archive="$sdk_dir/google-cloud-cli-588.0.0-linux-x86_64.tar.gz"
mkdir -p "$sdk_dir"
if [ ! -f "$sdk_archive" ]; then
  curl --fail --location --output "$sdk_archive" \
    https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-588.0.0-linux-x86_64.tar.gz
fi
printf '%s  %s\n' e38ceac43022bb5a4d94d5a4a9c9c51f90f28c910b59a018ae07011e220c4412 "$sdk_archive" | sha256sum --check
if [ ! -x "$sdk_dir/google-cloud-sdk/bin/gcloud" ]; then
  tar -xzf "$sdk_archive" -C "$sdk_dir"
fi
CLOUDSDK_CORE_DISABLE_FILE_LOGGING=true "$sdk_dir/google-cloud-sdk/bin/gcloud" version
