# Размещение Инфокода на VPS

## Что арендовать

Для старта: Ubuntu 24.04 LTS, 2 vCPU, 4 ГБ RAM, 40–60 ГБ SSD/NVMe, публичный IP. Это ориентир для 100 зарегистрированных учеников, задач и примерно 5 ГБ фото/файлов; фактическую одновременную нагрузку нужно измерить после запуска. Для большей одновременной активности рассмотрите 4 vCPU и 6–8 ГБ RAM. Видео потребует отдельного расчёта места и трафика.

Проверено по страницам провайдеров 11 октября 2026:

| Пример | Ресурсы | Цена на открытой странице |
|---|---|---|
| [Beget VPS](https://beget.com/ru/vps), Санкт-Петербург | 2 ядра, 4 ГБ, 40 ГБ, IPv4 | 33 + 5 ₽/день за IPv4, около 1 140 ₽ за 30 дней |
| [Beget VPS](https://beget.com/ru/vps), Санкт-Петербург | 4 ядра, 6 ГБ, 80 ГБ, IPv4 | 68 + 5 ₽/день за IPv4, около 2 190 ₽ за 30 дней |
| [Домен .ru в Beget](https://beget.com/ru/domains) | Регистрация / продление | 199 ₽ первый год / 420 ₽ в год продление |

Цены зависят от региона, периода оплаты и выбранных опций; проверьте итог в корзине. Плановый бюджет с небольшим резервом: 1 200–2 500 ₽ в месяц плюс домен. Работа администратора сервера в этот бюджет не включена. HTTPS-сертификат в предложенной конфигурации выпускается автоматически; отдельная покупка сертификата не требуется.

## Как устроена конфигурация

`compose.production.yaml` запускает PostgreSQL 16, приложение, миграции, обслуживание и Caddy. Наружу открыты только 80/443; база и приложение доступны во внутренней сети Docker. Caddy принимает HTTPS и передаёт запросы приложению. Подробнее: [автоматический HTTPS Caddy](https://caddyserver.com/docs/automatic-https).

Сервис `maintenance` каждую минуту завершает истёкшие попытки и один раз в сутки создаёт проверенный архив данных и файлов. Сохраняются последние 3 архива. Это копии на том же сервере: периодически скачивайте их на другое устройство или в отдельное хранилище. Для 5 ГБ материалов предусмотрите место на сами материалы, копии, систему и контейнеры.

Фото и видео копируются и проверяются небольшими порциями, без чтения целого файла в память. Готовое имя архив получает только после проверки: неудачное создание копии не блокирует следующую попытку и не удаляет прежние копии. При записи очередного архива временно требуется место ещё для одной полной копии.

## 1. Сервер, домен и Docker

1. Арендуйте VPS с Ubuntu 24.04, запишите IP.
2. Купите домен. В DNS создайте A-запись для выбранного имени, указывающую на IPv4 сервера. AAAA добавляйте только при настроенном IPv6.
3. Разрешите входящие TCP 22, 80, 443 в панели сервера. UDP 443 нужен для HTTP/3. Порты 5432 и 8000 наружу не открывайте.
4. Подключитесь из PowerShell: `ssh root@IP_СЕРВЕРА`.
5. Установите Docker Engine и Compose по [официальной инструкции для Ubuntu](https://docs.docker.com/engine/install/ubuntu/), либо используйте готовый образ Ubuntu с Docker у провайдера. Проверка: `docker compose version`.

## 2. Исходники

На сервере подготовьте утилиты:

```bash
apt-get update
apt-get install -y git python3 unzip
```

**Через GitHub.** Если проект уже загружен и на сервере настроен доступ к репозиторию:

```bash
mkdir -p /opt
cd /opt
git clone https://github.com/ВАШ_ЛОГИН/infocode.git
cd /opt/infocode
```

Для приватного репозитория потребуется отдельная авторизация GitHub на сервере; вход на компьютере не передаётся по SSH. Если пока не настраивали этот доступ, используйте второй способ.

**Через готовый архив — без авторизации GitHub на сервере.** В PowerShell на компьютере:

```powershell
scp C:\Users\tgpro\Documents\Codex\2026-09-28\yf\outputs\infocode-python.zip root@IP_СЕРВЕРА:/opt/infocode-python.zip
```

Затем в терминале сервера:

```bash
cd /opt
unzip infocode-python.zip
cd /opt/infocode
```

Это два альтернативных способа; выберите один. Архив содержит папку `infocode`, но не историю Git и не ваши данные. Перенос учеников и загруженных материалов описан ниже. В файлах проекта заменять `127.0.0.1` вручную не нужно: домен задаётся конфигурацией.

## 3. Настройки

В `/opt/infocode` на сервере выполните, заменив домен и email своими:

```bash
python3 deploy/configure.py --domain ege.example.ru --email teacher@example.ru
docker compose --env-file .env.production -f compose.production.yaml build
docker compose --env-file .env.production -f compose.production.yaml up -d db migrate
```

Скрипт создаёт `.env.production` с индивидуальными ключом приложения и паролем базы; повторно существующий файл не перезаписывает. Храните этот файл отдельно от GitHub. Не меняйте пароль базы в нём после первого запуска без отдельной смены пароля внутри PostgreSQL.

## 4. Выберите один способ наполнения

### Новый, пустой сайт

```bash
docker compose --env-file .env.production -f compose.production.yaml run --rm web python manage.py createsuperuser
```

Укажите собственный логин и пароль администратора. Демонстрационные материалы добавляются только по желанию:

```bash
docker compose --env-file .env.production -f compose.production.yaml run --rm web python manage.py seed_demo
```

### Перенос текущего сайта с компьютера

Этот путь выбирайте **вместо создания администратора и демонстрационных данных**: восстановление допускается только в пустую базу и пустую папку материалов.

На компьютере остановите старый сервер Ctrl+C. В папке проекта:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py backup_platform backups\transfer.zip
scp .\backups\transfer.zip root@IP_СЕРВЕРА:/opt/infocode/transfer.zip
```

Архив создаётся один раз; при повторном переносе выберите новое имя. На сервере, в `/opt/infocode`:

```bash
chmod 644 transfer.zip
docker compose --env-file .env.production -f compose.production.yaml run --rm -v /opt/infocode/transfer.zip:/tmp/transfer.zip:ro web python manage.py restore_platform /tmp/transfer.zip
chmod 600 transfer.zip
```

Пользователи, пароли, задания, курсы, история и файлы перенесутся вместе. Старые сеансы потребуют нового входа. `transfer.zip` содержит данные учеников: он исключён из Git и его не нужно выкладывать в репозиторий.

## 5. Запуск и проверка

```bash
docker compose --env-file .env.production -f compose.production.yaml up -d
docker compose --env-file .env.production -f compose.production.yaml ps
docker compose --env-file .env.production -f compose.production.yaml logs --tail=80 web caddy maintenance
```

Откройте `https://ВАШ_ДОМЕН/health/`: ожидается `{"status":"ok"}`. Затем проверьте вход, открытие задания и загрузку фото. Откройте кабинет преподавателя `/teacher/`, создайте тестового ученика или приглашение по [STUDENTS.md](STUDENTS.md).

Если HTTPS ещё не появился, проверьте DNS и доступность портов 80/443. Если контейнер `migrate` завершился с кодом 0 — это нормально: он выполнил миграции и закончил работу.

## Копии и обновления

Просмотр архивов:

```bash
docker compose --env-file .env.production -f compose.production.yaml exec maintenance ls -lh /backups
```

Скопировать выбранную копию на сервер в текущую папку (подставьте существующее имя):

```bash
docker compose --env-file .env.production -f compose.production.yaml cp maintenance:/backups/infocode-2026-10-11.zip ./
```

Затем скачайте её через `scp`. Для обновления сначала сделайте свежую копию и сохраните `.env.production`:

```bash
docker compose --env-file .env.production -f compose.production.yaml exec web python manage.py backup_platform /backups/before-update-01.zip
docker compose --env-file .env.production -f compose.production.yaml stop web maintenance caddy
git pull --ff-only
docker compose --env-file .env.production -f compose.production.yaml build
docker compose --env-file .env.production -f compose.production.yaml run --rm migrate
docker compose --env-file .env.production -f compose.production.yaml up -d
```

Для следующего обновления используйте новое имя копии. Если миграция завершилась ошибкой, остановитесь и посмотрите её вывод. Данные находятся в именованных томах Docker; не используйте `down -v`, эта команда удаляет тома с базой и материалами.

Команда `git pull` относится к установке через GitHub. При первой установке из ZIP вместо неё загрузите новый архив исходников и распакуйте его в ту же папку; перед заменой сохраните резервную копию и `.env.production`. Не загружайте поверх сервера локальную базу или папку `.venv`.

## Проверки этой конфигурации

Локально проверены Django, сценарии аккаунтов, генератор настроек и структура YAML. На текущем компьютере нет Docker, поэтому сборка контейнеров и выпуск сертификата здесь не выполнялись. В GitHub Actions подготовлены проверки SQLite/PostgreSQL, сборка образа, запуск приложения с PostgreSQL, миграциями и обслуживанием, проверка Caddy. После первого push откройте вкладку Actions и дождитесь успешного выполнения, затем проверяйте реальный сервер. Workflow проверяет код; он не публикует сайт автоматически.
