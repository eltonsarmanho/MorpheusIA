#!/usr/bin/env bash
# Gera os PNGs das três páginas do protótipo em painel_gerencial.html.
# Uso: ./gerar_png.sh   (requer google-chrome ou chromium, e python3 com Pillow)
set -euo pipefail
cd "$(dirname "$0")"
CHROME=$(command -v google-chrome || command -v chromium || command -v chromium-browser)
URL="file://$PWD/painel_gerencial.html"
shot() {  # página, arquivo, altura da janela
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
    --window-size=1440,"$3" --virtual-time-budget=1500 \
    --screenshot="$PWD/$2" "$URL#$1" >/dev/null 2>&1
  # O Chrome sem interface reserva uma faixa vazia abaixo da página: corta onde o menu lateral termina.
  python3 - "$2" <<'PY'
import sys
from PIL import Image
f = sys.argv[1]
im = Image.open(f).convert("RGB")
y = im.height - 1
while y > 0 and im.getpixel((20, y))[0] > 60:
    y -= 1
im.crop((0, 0, im.width, y + 1)).save(f, optimize=True)
print(f, im.width, y + 1)
PY
}
shot visao     painel_visao_geral.png 800
shot qualidade painel_qualidade.png   800
shot detalhes  painel_detalhes.png    1320
