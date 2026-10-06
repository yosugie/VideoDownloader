#!/usr/bin/env bash
#
# Управление ботом одной командой:
#
#     sudo /opt/videobot/app/deploy/bot.sh stop     # выключить совсем
#     sudo /opt/videobot/app/deploy/bot.sh start    # включить обратно
#     sudo /opt/videobot/app/deploy/bot.sh status   # что сейчас
#     sudo /opt/videobot/app/deploy/bot.sh logs     # живой журнал
#
# "stop" освобождает всё: процесс завершается, память и канал свободны,
# после перезагрузки сервера бот сам не поднимется до "start".
#
# Если нужно просто перестать принимать загрузки, не трогая службу,
# удобнее команда /pause прямо в Telegram.

set -euo pipefail

SERVICE="${SERVICE:-videodownloader}"
ACTION="${1:-status}"

case "${ACTION}" in
    start)
        systemctl enable --now "${SERVICE}"
        echo "✅ Бот запущен и будет подниматься после перезагрузки"
        ;;
    stop)
        systemctl disable --now "${SERVICE}"
        echo "⛔️ Бот остановлен и сам больше не поднимется"
        ;;
    restart)
        systemctl restart "${SERVICE}"
        echo "🔄 Перезапущен"
        ;;
    status)
        systemctl status "${SERVICE}" --no-pager || true
        ;;
    logs)
        journalctl -u "${SERVICE}" -f
        ;;
    *)
        echo "Как пользоваться: $0 {start|stop|restart|status|logs}" >&2
        exit 1
        ;;
esac
