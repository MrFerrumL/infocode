# Как загрузить проект в GitHub

GitHub хранит исходный код. Рабочий сайт с Python и базой нужно запускать на VPS; GitHub Pages для этого проекта не подходит.

Локальный репозиторий находится непосредственно в папке `infocode`, ветка — `main`. Файлы базы, фото/видео, окружение `.venv`, пароли, локальные ключи, `.env`, журналы, настройки IDE и резервные копии исключены через `.gitignore`.

## Первая загрузка

1. Войдите в свой GitHub и создайте пустой **приватный** репозиторий `infocode`. Не добавляйте README, лицензию и `.gitignore` на сайте: эти файлы проекта уже подготовлены. [Инструкция GitHub](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository).
2. Скопируйте HTTPS-ссылку репозитория.
3. В PowerShell в папке проекта выполните, подставив свой логин:

```powershell
cd C:\Users\tgpro\Documents\Codex\2026-09-28\yf\outputs\infocode
git remote add origin https://github.com/ВАШ_ЛОГИН/infocode.git
git push -u origin main
```

В этой рабочей папке Git уже настроен, исходники сохранены в первом коммите. Повторно выполнять `git init` не требуется. Если вы начинаете из скачанного ZIP на другом компьютере, история Git туда не включена: сначала выполните `git init -b main`, `git add .` и `git commit -m "Initial platform"`, затем команды `remote add` и `push` выше.

Если `origin` уже существует, сначала посмотрите `git remote -v`: не заменяйте чужой адрес вслепую. При запросе входа используйте окно авторизации Git Credential Manager / GitHub. Пароль аккаунта GitHub не заменяет токен для Git HTTPS; токены не вставляйте в адрес репозитория и не сохраняйте в файлах проекта.

Если графическая авторизация Git не работает, установите GitHub CLI с [официального сайта](https://cli.github.com/), затем в собственном терминале:

```powershell
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
git push -u origin main
```

## Обновления

```powershell
git status
git add .
git diff --cached --stat
git commit -m "Update platform"
git push
```

После загрузки в разделе **Actions** запускаются тесты. На сервер изменения попадают только после отдельного обновления по [DEPLOYMENT.md](DEPLOYMENT.md). Архив с учениками и материалами переносите отдельно, не через GitHub.

## Если Git сообщает об ошибке

- `detected dubious ownership`: проект мог быть создан под служебным пользователем. Для этой конкретной папки выполните `git config --global --add safe.directory C:/Users/tgpro/Documents/Codex/2026-09-28/yf/outputs/infocode`, затем повторите команду Git. Не добавляйте исключение `*` для всех каталогов.

- `git` не найден: установите [Git for Windows](https://git-scm.com/downloads/win) и заново откройте терминал.
- `remote origin already exists`: выполните `git remote -v`. Если там ваш правильный репозиторий, сразу переходите к `git push -u origin main`.
- `Repository not found`: проверьте адрес, существование репозитория и вход именно в аккаунт с доступом к нему.
- `Authentication failed` или ошибка Credential Manager: используйте вход через `gh auth login`, описанный выше. Пароль от самого сайта «Инфокод» для GitHub не подходит.
- `rejected` / `fetch first`: в удалённом репозитории уже есть история. Не используйте `--force`; нужно сначала согласовать обе версии. Для первой публикации проще создать пустой репозиторий, как в шаге 1.
