# Установка на сервер (Ubuntu)

Инструкция написана для сервера, который уже чем-то занят — например, работает
как VPN. Главная задача: поставить бота рядом, ничего не задев.

## Что делаем и чего не делаем

**Не трогаем:**

- правила файрвола и `iptables` — бот только ходит наружу;
- сетевые интерфейсы, маршруты, пересылку пакетов;
- порты — бот ничего не слушает, он сам подключается к Telegram;
- системный Python — все библиотеки ставятся в отдельное окружение.

**Делаем:**

- ставим пакеты `python3-venv`, `ffmpeg`, `git` (в сеть не лезут);
- заводим отдельного пользователя `videobot` без права входа;
- кладём всё в `/opt/videobot` — одна папка, которую целиком можно удалить;
- регистрируем systemd-службу, чтобы бот пережил перезагрузку.

> **Почему не Docker.** При установке Docker правит `iptables`, поднимает свою
> сеть и меняет политику цепочки FORWARD на DROP. На обычном сервере это
> проходит незаметно, на VPN-сервере — ломает пересылку трафика. Поэтому
> здесь systemd: он к сети не прикасается.

---

## ⚠️ Сначала остановите бота на компьютере

У одного токена может быть только **один** работающий экземпляр. Если бот
останется запущен на ноутбуке, серверный и локальный начнут отбирать друг у
друга сообщения, и оба будут сыпать ошибками.

На ноутбуке: **Ctrl+C** в терминале с ботом.

---

## Шаг 0. Осмотреться

```bash
python3 --version                 # нужен 3.11 или новее
df -h /                           # хватает ли места
systemctl list-units --type=service --state=running --no-pager | head -20
```

Ubuntu 24.04 приносит Python 3.12 — этого достаточно.

## Шаг 1. Пакеты

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip ffmpeg git
```

`ffmpeg` тянет за собой кодеки, но к сети не имеет отношения.

## Шаг 2. Отдельный пользователь и каталог

```bash
sudo useradd --system --create-home --home-dir /opt/videobot \
     --shell /usr/sbin/nologin videobot
```

Войти под этим пользователем нельзя — он существует только чтобы запускать бота
с минимальными правами.

## Шаг 3. Код и зависимости

```bash
sudo -u videobot git clone https://github.com/yosugie/VideoDownloader.git \
     /opt/videobot/app

sudo -u videobot python3 -m venv /opt/videobot/app/.venv
sudo -u videobot /opt/videobot/app/.venv/bin/pip install --upgrade pip
sudo -u videobot /opt/videobot/app/.venv/bin/pip install \
     -r /opt/videobot/app/requirements.txt
```

## Шаг 4. Настройки

```bash
sudo -u videobot cp /opt/videobot/app/.env.example /opt/videobot/app/.env
sudo nano /opt/videobot/app/.env
```

Заполните как минимум:

```env
BOT_TOKEN=токен от @BotFather
ALLOWED_USER_IDS=ваш id
ADMIN_IDS=ваш id
MAX_CONCURRENT_DOWNLOADS=3
```

На сервере канал шире, чем дома, поэтому одновременных загрузок можно поставить
побольше. Сохранить: **Ctrl+O**, **Enter**, **Ctrl+X**.

Файл содержит токен, поэтому закрываем его от посторонних:

```bash
sudo chown videobot:videobot /opt/videobot/app/.env
sudo chmod 600 /opt/videobot/app/.env
```

## Шаг 5. Служба systemd

```bash
sudo cp /opt/videobot/app/deploy/videodownloader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now videodownloader
```

`enable --now` означает «запустить сейчас и поднимать при загрузке сервера».

## Шаг 6. Проверка

```bash
systemctl status videodownloader --no-pager
sudo journalctl -u videodownloader -n 30 --no-pager
```

`sudo` у journalctl обязателен. Служба работает от пользователя `videobot`,
а обычный администратор видит в журнале только свои записи — без `sudo`
вывод будет пустым (`-- No entries --`), хотя бот при этом прекрасно
работает.

Чтобы не писать `sudo` каждый раз, можно добавить себя в группу `adm`:

```bash
sudo usermod -aG adm $USER
```

Изменение вступит в силу после нового входа по SSH.

Ждём строку вида:

```
VideoDownloader 1.0.0 запущен как @ваш_бот (доступ: белый список)
```

Теперь напишите боту в Telegram — он отвечает уже с сервера, ноутбук можно
закрывать.

**Убедитесь, что VPN жив**: подключитесь с телефона или ноутбука как обычно.
Мы ничего сетевого не меняли, но проверить стоит.

---

## Новые настройки не приезжают с обновлением

`git pull` обновляет `.env.example`, но **никогда не трогает ваш `.env`** — он
в `.gitignore`. Если в проекте появилась новая настройка, её нужно дописать в
свой файл руками:

```bash
sudo tee -a /opt/videobot/app/.env >/dev/null <<'EOF'
DAILY_LIMIT_PER_USER=20
EOF
sudo systemctl restart videodownloader
```

Сверить, что именно задано:

```bash
sudo grep -vE '^\s*(#|$)' /opt/videobot/app/.env
```

## Бот уступает дорогу главной службе

В юните заданы `Nice`, `CPUWeight` и `IOWeight`: при споре за процессор и
диск бот получает меньшую долю, чем остальные службы. Когда сервер свободен,
он работает в полную силу — ограничения включаются только в момент нехватки.

Память ограничена `MemoryHigh=800M` и `MemoryMax=1200M`. На сервере с двумя
гигабайтами это защита от ситуации, когда несколько тяжёлых загрузок разом
оставляют систему без памяти: пусть лучше сорвётся одна загрузка.

Проверить, сколько бот потребляет сейчас:

```bash
systemctl status videodownloader --no-pager | grep -E 'Memory|CPU'
```

### Журнал тоже занимает диск

По умолчанию systemd отдаёт журналу до десятой части раздела — на диске в
20 ГБ это два гигабайта. Стоит ограничить:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/size.conf >/dev/null <<'EOF'
[Journal]
SystemMaxUse=200M
EOF
sudo systemctl restart systemd-journald
```

## Включить и выключить

```bash
sudo /opt/videobot/app/deploy/bot.sh stop     # остановить совсем
sudo /opt/videobot/app/deploy/bot.sh start    # запустить обратно
sudo /opt/videobot/app/deploy/bot.sh status   # состояние
sudo /opt/videobot/app/deploy/bot.sh logs     # живой журнал
```

`stop` не только останавливает бота, но и снимает его с автозапуска, так что
после перезагрузки сервера он сам не поднимется — до `start`.

Если нужно лишь перестать принимать загрузки, не трогая службу, удобнее
команда `/pause` прямо в Telegram: она доступна администраторам и не требует
заходить по SSH.

## Повседневные команды

```bash
sudo journalctl -u videodownloader -f     # живые логи, выход Ctrl+C
systemctl status videodownloader          # состояние
sudo systemctl restart videodownloader    # перезапуск
sudo systemctl stop videodownloader       # остановить
```

## Обновление

Одной командой:

```bash
sudo /opt/videobot/app/deploy/update.sh
```

Скрипт забирает изменения, доустанавливает зависимости, если они менялись,
перезапускает службу и показывает последние строки журнала. Пароль `sudo`
спрашивается один раз — внутри всё выполняется уже от root, а работа с кодом
идёт от пользователя `videobot`, чтобы права на файлы не перемешались.

Если служба после перезапуска не поднялась, скрипт покажет журнал и завершится
с ошибкой.

То же самое вручную, если скрипт почему-то недоступен:

```bash
sudo -u videobot git -C /opt/videobot/app pull
sudo -u videobot /opt/videobot/app/.venv/bin/pip install \
     -r /opt/videobot/app/requirements.txt
sudo systemctl restart videodownloader
```

Команды написаны с полными путями намеренно. Каталог `/opt/videobot` —
домашний для пользователя `videobot`, и Ubuntu создаёт такие каталоги
закрытыми для посторонних, поэтому обычный `cd` туда не пройдёт даже у
администратора. Флаг `git -C` указывает каталог, не заходя в него.

Если хочется заглядывать внутрь руками, каталог можно приоткрыть:

```bash
sudo chmod 755 /opt/videobot
```

На безопасность это почти не влияет: `.env` с токеном остаётся доступным
только владельцу (режим 600).

## Новые настройки не приезжают с обновлением

`git pull` обновляет `.env.example`, но **никогда не трогает ваш `.env`** — он
в `.gitignore`. Если в проекте появилась новая настройка, её нужно дописать в
свой файл руками:

```bash
sudo tee -a /opt/videobot/app/.env >/dev/null <<'EOF'
DAILY_LIMIT_PER_USER=20
EOF
sudo systemctl restart videodownloader
```

Сверить, что именно задано:

```bash
sudo grep -vE '^\s*(#|$)' /opt/videobot/app/.env
```

## Повседневные команды

```bash
sudo journalctl -u videodownloader -f     # живые логи, выход Ctrl+C
systemctl status videodownloader          # состояние
sudo systemctl restart videodownloader    # перезапуск
sudo systemctl stop videodownloader       # остановить
```

## Обновление

```bash
sudo -u videobot git -C /opt/videobot/app pull
sudo -u videobot /opt/videobot/app/.venv/bin/pip install \
     -r /opt/videobot/app/requirements.txt
sudo systemctl restart videodownloader
```

Команды написаны с полными путями намеренно. Каталог `/opt/videobot` —
домашний для пользователя `videobot`, и Ubuntu создаёт такие каталоги
закрытыми для посторонних, поэтому обычный `cd` туда не пройдёт даже у
администратора. Флаг `git -C` указывает каталог, не заходя в него.

Если хочется заглядывать внутрь руками, каталог можно приоткрыть:

```bash
sudo chmod 755 /opt/videobot
```

На безопасность это почти не влияет: `.env` с токеном остаётся доступным
только владельцу (режим 600).

## Cookies на сервере

Браузера на сервере нет, поэтому `COOKIES_FROM_BROWSER` там не работает —
нужен файл. Экспортируйте `cookies.txt` на компьютере (см. раздел про cookies
в README) и скопируйте:

```bash
# на компьютере
scp cookies.txt admin@СЕРВЕР:/tmp/cookies.txt

# на сервере
sudo mv /tmp/cookies.txt /opt/videobot/app/cookies.txt
sudo chown videobot:videobot /opt/videobot/app/cookies.txt
sudo chmod 600 /opt/videobot/app/cookies.txt
```

И в `.env`:

```env
COOKIES_FILE=cookies.txt
```

После перезапуска бот сообщит в логах, сколько записей увидел и для каких
доменов.

> ⚠️ Cookies были выпущены для вашего домашнего адреса, а предъявляться будут с
> адреса сервера. Для Instagram это выглядит как внезапный переезд в другую
> страну, и подозрительность к аккаунту вырастает. Ещё одна причина использовать
> отдельный аккаунт, а не основной.

## Если сервер работает как VPN

Трафик бота пойдёт с того же адреса, что и выход VPN. YouTube относится к таким
адресам настороженно и чаще требует подтвердить, что запрос не от робота. Если
это начнётся — помогут cookies, как и в случае с Instagram.

## Удалить всё без следа

```bash
sudo systemctl disable --now videodownloader
sudo rm /etc/systemd/system/videodownloader.service
sudo systemctl daemon-reload
sudo rm -rf /opt/videobot
sudo userdel videobot
```

Пакеты `ffmpeg` и `python3-venv` при желании убираются отдельно
(`sudo apt remove ffmpeg`), но они никому не мешают.
