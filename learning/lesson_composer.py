"""Presentation presets for a freely ordered lesson, not fixed lesson templates."""
SLIDE_TYPES = [
    {'key': 'text', 'label': 'Теория', 'icon': 'Тт', 'kind': 'text', 'hint': 'Текст, фото и любые материалы'},
    {'key': 'notebook', 'label': 'Конспект', 'icon': '▤', 'kind': 'notebook', 'hint': 'Готовый конспект с текстом и Python-ячейками'},
    {'key': 'quiz', 'label': 'Тест', 'icon': '☑', 'kind': 'quiz', 'quiz_mode': 'single', 'hint': 'Один правильный ответ'},
    {'key': 'multiple', 'label': 'Несколько ответов', 'icon': '☷', 'kind': 'quiz', 'quiz_mode': 'multiple', 'hint': 'Выбор нескольких вариантов'},
    {'key': 'boolean', 'label': 'Верно / неверно', 'icon': '✓', 'kind': 'quiz', 'quiz_mode': 'single', 'hint': 'Два варианта ответа'},
    {'key': 'answer', 'label': 'Практика', 'icon': '{ }', 'kind': 'answer', 'hint': 'Задача с ответом и, по желанию, кодом'},
    {'key': 'mixed', 'label': 'Теория + задание', 'icon': '⊞', 'kind': 'answer', 'hint': 'Материал и проверка на одном слайде'},
    {'key': 'video', 'label': 'Видео', 'icon': '▷', 'kind': 'video', 'hint': 'Видео с пояснениями и файлами'},
    {'key': 'numeric', 'label': 'Числовой ответ', 'icon': '123', 'kind': 'numeric', 'hint': 'Проверка числа с погрешностью'},
    {'key': 'gaps', 'label': 'Пропуски', 'icon': '…', 'kind': 'gaps', 'hint': 'Заполнение пропусков'},
    {'key': 'matching', 'label': 'Сопоставление', 'icon': '⇄', 'kind': 'matching', 'hint': 'Найти соответствия'},
    {'key': 'file', 'label': 'Файлы', 'icon': '↓', 'kind': 'file', 'hint': 'Материалы для скачивания'},
]
SLIDE_TYPES_BY_KEY = {item['key']: item for item in SLIDE_TYPES}
MAX_INITIAL_SLIDES = 60
