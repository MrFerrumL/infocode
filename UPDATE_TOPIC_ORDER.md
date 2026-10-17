# Исправление порядка номеров на действующем VPS

В кабинете и каталоге номера должны идти от 1 до 27. Исправлена сортировка запроса с подсчётом заданий. Миграции и перенумерация записей не требуются.

Выполните две команды в PowerShell на компьютере. Используется уже настроенный сервер `195.209.211.242` и существующая `.env.production`.

## 1. Передать исправленный файл

```powershell
scp -o IdentitiesOnly=yes -o ServerAliveInterval=30 -i "$env:USERPROFILE\Downloads\privatekey-1152809.pem" "C:\Users\tgpro\Documents\Codex\2026-09-28\yf\outputs\infocode\learning\views.py" ubuntu@195.209.211.242:/home/ubuntu/infocode-views.py
```

Дождитесь завершения без ошибки перед следующей командой.

## 2. Сохранить копию исходника, собрать и запустить обновление

```powershell
ssh -o IdentitiesOnly=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=6 -i "$env:USERPROFILE\Downloads\privatekey-1152809.pem" ubuntu@195.209.211.242 "cd /opt/infocode && sudo mkdir -p /opt/infocode-code-backups && sudo cp -n learning/views.py /opt/infocode-code-backups/views-before-topic-order.py && sudo install -m 644 /home/ubuntu/infocode-views.py learning/views.py && sudo docker compose --env-file .env.production -f compose.production.yaml build web && sudo docker compose --env-file .env.production -f compose.production.yaml up -d && sudo docker compose --env-file .env.production -f compose.production.yaml ps -a"
```

Сборка может занять несколько минут. После её завершения откройте `https://infocode1.ru/` и `https://infocode1.ru/catalog/` и обновите страницу сочетанием Ctrl+F5. Убедитесь, что номера расположены по возрастанию. Данные PostgreSQL и загруженные файлы остаются в существующих томах Docker. Команда не создаёт новых аккаунтов и не меняет пароль.

При ошибке сохраните вывод: обновление нельзя считать подтверждённым только по передаче файла. Команда `down -v` для этого исправления не нужна.
