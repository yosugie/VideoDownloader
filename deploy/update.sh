#!/usr/bin/env bash
#
# Обновление бота одной командой:
#
#     sudo /opt/videobot/app/deploy/update.sh
#
# Скрипт запускается от root, поэтому пароль спрашивается один раз, а не
# на каждом шаге. Внутри вся работа с кодом идёт от пользователя videobot,
# чтобы права на файлы не перемешались.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/videobot/app}"
APP_USER="${APP_USER:-videobot}"
SERVICE="${SERVICE:-videodownloader}"

if [[ ${EUID} -ne 0 ]]; then
    echo "Запускать нужно через sudo: sudo ${APP_DIR}/deploy/update.sh" >&2
    exit 1
fi

if [[ ! -d "${APP_DIR}/.git" ]]; then
    echo "Каталог ${APP_DIR} не похож на репозиторий." >&2
    exit 1
fi

echo "==> Забираю изменения"
if ! sudo -u "${APP_USER}" git -C "${APP_DIR}" pull --ff-only; then
    echo >&2
    echo "❌ Не удалось забрать изменения — бот остался на прежней версии." >&2
    echo "   Если git жалуется на файл, который будет перезаписан, уберите его" >&2
    echo "   в сторону и повторите:" >&2
    echo "     sudo mv ${APP_DIR}/ИМЯ_ФАЙЛА /tmp/" >&2
    exit 1
fi

echo "==> Проверяю зависимости"
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install --quiet \
     --requirement "${APP_DIR}/requirements.txt"

# yt-dlp и gallery-dl обновляются отдельно и всегда. Они живут за счёт
# того, что подстраиваются под вёрстку сайтов, а она меняется чаще, чем
# выходят наши изменения: вчерашняя версия сегодня может не скачать
# ничего с TikTok.
echo "==> Обновляю загрузчики"
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install --quiet --upgrade \
     yt-dlp gallery-dl curl-cffi
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/python" - <<'PY'
import gallery_dl, yt_dlp
print(f"    yt-dlp {yt_dlp.version.__version__}, gallery-dl {gallery_dl.version.__version__}")
try:
    import curl_cffi
    print(f"    curl-cffi {curl_cffi.__version__} — проверки TikTok проходятся")
except ImportError:
    print("    curl-cffi НЕ УСТАНОВЛЕН — TikTok работать не будет")
PY

echo "==> Перезапускаю службу"
systemctl restart "${SERVICE}"

# Служба поднимается не мгновенно, поэтому даём ей секунду.
sleep 1
if systemctl is-active --quiet "${SERVICE}"; then
    echo "==> Готово, бот работает"
    journalctl -u "${SERVICE}" -n 5 --no-pager
else
    echo "==> Служба не поднялась, смотрим журнал:" >&2
    journalctl -u "${SERVICE}" -n 30 --no-pager >&2
    exit 1
fi
