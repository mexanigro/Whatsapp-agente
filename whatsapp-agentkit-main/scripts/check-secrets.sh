#!/usr/bin/env bash
# scripts/check-secrets.sh — Escanea archivos staged por secretos antes de hacer commit.
#
# Uso directo:   bash scripts/check-secrets.sh
# Como git hook: cp scripts/check-secrets.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

set -euo pipefail

ROJO='\033[0;31m'
AMARILLO='\033[1;33m'
VERDE='\033[0;32m'
RESET='\033[0m'

errores=0

# ── Archivos staged que se van a commitear ────────────────────────────────────
if git rev-parse --verify HEAD >/dev/null 2>&1; then
    STAGED=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null || true)
else
    # Primer commit: revisar todos los archivos en el index
    STAGED=$(git ls-files --cached 2>/dev/null || true)
fi

if [ -z "$STAGED" ]; then
    echo -e "${VERDE}✓ No hay archivos staged para revisar.${RESET}"
    exit 0
fi

echo "Escaneando secretos en archivos staged..."

# ── 1. Archivos .env nunca deben commitearse ──────────────────────────────────
while IFS= read -r archivo; do
    basename=$(basename "$archivo")
    if [[ "$basename" == ".env" ]] || [[ "$basename" =~ ^\.env\. && "$basename" != ".env.example" ]]; then
        echo -e "${ROJO}ERROR: Archivo de secretos en staging: $archivo${RESET}"
        echo "  Agrega al .gitignore: .env .env.*"
        errores=$((errores + 1))
    fi
done <<< "$STAGED"

# ── 2. Patrones de API keys conocidas ─────────────────────────────────────────
declare -A PATRONES=(
    ["Anthropic API key"]="sk-ant-api[0-9A-Za-z-]{20,}"
    ["OpenAI API key"]="sk-[A-Za-z0-9]{20,}"
    ["ElevenLabs API key"]="[a-f0-9]{32}"
    ["Twilio Auth Token"]="[a-f0-9]{32}"
    ["AWS Access Key"]="AKIA[0-9A-Z]{16}"
    ["AWS Secret Key"]="[A-Za-z0-9+/]{40}"
    ["Firebase service account"]="\"private_key\""
    ["Generic bearer token"]="Bearer [A-Za-z0-9._-]{20,}"
    ["Generic password en .env"]="PASSWORD=[A-Za-z0-9!@#$%^&*]{8,}"
    ["Generic secret en .env"]="SECRET=[A-Za-z0-9!@#$%^&*]{8,}"
    ["Generic token en .env"]="_TOKEN=[A-Za-z0-9!@#$%^&*]{8,}"
)

# Extensiones de archivos binarios a ignorar
IGNORAR_EXT=("png" "jpg" "jpeg" "gif" "ico" "pdf" "zip" "tar" "gz" "ogg" "mp3" "mp4" "db" "sqlite")

while IFS= read -r archivo; do
    # Ignorar archivos binarios
    ext="${archivo##*.}"
    es_binario=false
    for ext_ignorar in "${IGNORAR_EXT[@]}"; do
        if [[ "$ext" == "$ext_ignorar" ]]; then
            es_binario=true
            break
        fi
    done
    $es_binario && continue

    # Ignorar .env.example (es el template publico)
    [[ "$archivo" == *".env.example" ]] && continue

    # Obtener contenido del archivo staged (no el del working tree)
    contenido=$(git show ":$archivo" 2>/dev/null || true)
    [ -z "$contenido" ] && continue

    for nombre in "${!PATRONES[@]}"; do
        patron="${PATRONES[$nombre]}"
        if echo "$contenido" | grep -qP "$patron" 2>/dev/null; then
            echo -e "${AMARILLO}ADVERTENCIA: Posible secreto ($nombre) en: $archivo${RESET}"
            echo "  Patron: $patron"
            echo "  Si es un falso positivo, agrega el archivo a .gitignore o usa 'git commit --no-verify' (con cuidado)"
            errores=$((errores + 1))
        fi
    done
done <<< "$STAGED"

# ── 3. Archivos de credenciales conocidos ─────────────────────────────────────
ARCHIVOS_PELIGROSOS=("serviceAccountKey.json" "credentials.json" "firebase-adminsdk*.json" "*.pem" "*.key" "*.p12")

while IFS= read -r archivo; do
    basename=$(basename "$archivo")
    for patron in "${ARCHIVOS_PELIGROSOS[@]}"; do
        if [[ "$basename" == $patron ]]; then
            echo -e "${ROJO}ERROR: Archivo de credenciales en staging: $archivo${RESET}"
            errores=$((errores + 1))
        fi
    done
done <<< "$STAGED"

# ── Resultado ─────────────────────────────────────────────────────────────────
if [ "$errores" -gt 0 ]; then
    echo ""
    echo -e "${ROJO}✗ Se encontraron $errores problema(s) de seguridad. Commit bloqueado.${RESET}"
    echo "  Si estás seguro de que no hay secretos reales, usa: git commit --no-verify"
    exit 1
else
    echo -e "${VERDE}✓ Sin secretos detectados en los archivos staged.${RESET}"
    exit 0
fi
