# HYROX Seoul Ticket Agent

Агент два раза в день проверяет билеты HYROX Seoul и пишет в Telegram, если появляется возможность купить нужную категорию.

Цель по умолчанию:

- событие: `HYROX Seoul`
- месяц: `November 2026`
- категория: `HYROX Doubles Mixed / Open Mixed`
- страница: `https://hyroxsa.com/event/hyrox-seoul/`

## Как это работает

Агент живет в GitHub Actions, поэтому не зависит от MacBook. Компьютер может быть выключен.

Два раза в день GitHub запускает:

```bash
python hyrox-ticket-agent/check_hyrox.py
```

Скрипт:

1. открывает страницу HYROX Seoul;
2. ищет признаки открытия продаж и нужной категории;
3. сравнивает состояние с прошлой проверкой;
4. если билеты стали доступны или статус изменился на подозрительно хороший, отправляет Telegram-сообщение;
5. сохраняет состояние в `hyrox-ticket-agent/state/status.json`.

## Что тебе нужно сделать

### 1. Создать Telegram-бота

В Telegram открой `@BotFather`:

1. `/newbot`
2. имя, например `HYROX Seoul Watcher`
3. username, например `hyrox_seoul_watch_bot`
4. скопируй token

### 2. Узнать свой chat_id

Напиши своему новому боту любое сообщение, например:

```text
start
```

Потом локально запусти:

```bash
cd "/Users/mikeymike/Documents/New project 2"
python3 hyrox-ticket-agent/get_telegram_chat_id.py "PASTE_BOT_TOKEN_HERE"
```

Скрипт покажет `chat_id`.

### 3. Создать GitHub repo

Самый простой путь:

1. Создай приватный репозиторий на GitHub, например `hyrox-seoul-ticket-agent`.
2. Загрузи туда папку `hyrox-ticket-agent` и папку `.github/workflows`.
3. В GitHub открой `Settings -> Secrets and variables -> Actions -> New repository secret`.
4. Добавь:

```text
TELEGRAM_BOT_TOKEN = token от BotFather
TELEGRAM_CHAT_ID = твой chat_id
```

### 4. Включить Actions

В репозитории открой вкладку `Actions` и включи workflows, если GitHub попросит.

Агент будет проверять билеты два раза в день.

## Ручная проверка

В GitHub можно нажать:

`Actions -> HYROX Seoul Ticket Watcher -> Run workflow`

Если хочешь тестовое сообщение даже без доступных билетов, временно добавь repository secret:

```text
NOTIFY_EVERY_RUN = true
```

Потом удали, чтобы не спамило.

## Настройки

Меняются в `.github/workflows/hyrox-ticket-agent.yml`:

```yaml
HYROX_EVENT_URL: "https://hyroxsa.com/event/hyrox-seoul/"
TARGET_CATEGORY: "HYROX DOUBLES MIXED|Doubles Mixed|Open Mixed|Mixed"
```

Если HYROX перенесет покупку на отдельный checkout URL, добавь:

```yaml
HYROX_TICKET_URL: "https://..."
```

## Важный нюанс

HYROX может открыть продажи через отдельную очередь, JS checkout или партнёрскую платформу. Тогда простой HTML-мониторинг может увидеть только общий сигнал “sales open”, но не точное наличие категории.

Если такое случится, следующий уровень — Playwright-агент, который как браузер заходит в checkout и проверяет конкретную кнопку категории.
