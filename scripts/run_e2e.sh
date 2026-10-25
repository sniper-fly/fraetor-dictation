#!/usr/bin/env bash
# 実音声E2E (tests/e2e) を、AWS SSM から取得した資格情報で実行する。
#
# 前提: `aws sso login --profile <profile>` 済み。
# 資格情報はこのプロセスの環境変数にだけ置き、ファイルや標準出力には出さない。
#
# 必須の環境変数:
#   AWS_PROFILE                  使うAWSプロファイル
#   FRAETOR_DICTATION_AUDIO_DIR  WAV (16kHz/16bit/mono) を置いたフォルダ
#   DICTATION_SSM_MAI_ENDPOINT   MAIエンドポイントのSSMパラメータ名
#   DICTATION_SSM_MAI_API_KEY    MAI APIキーのSSMパラメータ名
# 引数はそのまま pytest に渡す (例: scripts/run_e2e.sh -s -k long)。
set -euo pipefail

: "${AWS_PROFILE:?AWS_PROFILE を指定してください}"
: "${FRAETOR_DICTATION_AUDIO_DIR:?FRAETOR_DICTATION_AUDIO_DIR を指定してください}"
: "${DICTATION_SSM_MAI_ENDPOINT:?DICTATION_SSM_MAI_ENDPOINT を指定してください}"
: "${DICTATION_SSM_MAI_API_KEY:?DICTATION_SSM_MAI_API_KEY を指定してください}"

get_param() {
  aws ssm get-parameter --with-decryption --name "$1" \
    --query Parameter.Value --output text
}

FRAETOR_DICTATION_MAI_ENDPOINT="$(get_param "$DICTATION_SSM_MAI_ENDPOINT")"
FRAETOR_DICTATION_MAI_API_KEY="$(get_param "$DICTATION_SSM_MAI_API_KEY")"
export FRAETOR_DICTATION_MAI_ENDPOINT FRAETOR_DICTATION_MAI_API_KEY

cd "$(dirname "$0")/.."
exec uv run pytest tests/e2e "$@"
