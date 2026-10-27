from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator, FileExtensionValidator
from django.db import models
from .python_answers import CODE_MODES, CODE_HELP, MAX_CODE_LENGTH
from .subjects import SubjectManager, current as current_subject, subject_field


def file_size(value):
    if value.size > 20 * 1024 * 1024:
        raise ValidationError('Файл должен быть не больше 20 МБ.')


def data_file_size(value):
    # Task data files (e.g. number 17 sequences) can exceed the general 20 MB limit.
    if value.size > 50 * 1024 * 1024:
        raise ValidationError('Файл должен быть не больше 50 МБ.')


def video_size(value):
    if value.size > 500 * 1024 * 1024:
        raise ValidationError('Видео должно быть не больше 500 МБ.')


class ActiveManager(models.Manager):
    def get_queryset(self):
        qs = super().get_queryset().filter(archived_at=None)
        parents = {'taskblock': ['task'], 'section': ['course'], 'lesson': ['section', 'section__course'], 'step': ['lesson', 'lesson__section', 'lesson__section__course']}
        for parent in parents.get(self.model._meta.model_name, []):
            qs = qs.filter(**{parent+'__archived_at': None})
        if self.model._meta.model_name == 'task':
            qs = qs.filter(topic__subject=current_subject())
        elif any(f.name == 'subject' for f in self.model._meta.fields):
            qs = qs.filter(subject=current_subject())
        return qs


class Topic(models.Model):
    objects = SubjectManager()
    all_objects = models.Manager()
    subject = subject_field()
    number = models.PositiveSmallIntegerField('Номер ЕГЭ', validators=[MinValueValidator(1), MaxValueValidator(27)])
    title = models.CharField('Тема', max_length=150)
    description = models.TextField('Описание', blank=True)
    class Meta:
        ordering = ['number']
        constraints = [models.UniqueConstraint(fields=['subject', 'number'], name='unique_subject_topic_number')]
        verbose_name = 'тема'
        verbose_name_plural = 'темы'
    def __str__(self):
        return f'{self.number:02} · {self.title}'


class Task(models.Model):
    objects = ActiveManager()
    all_objects = models.Manager()
    LEVELS = [('easy', 'Простой'), ('medium', 'Средний'), ('hard', 'Сложный'), ('ege', 'ЕГЭ'), ('grave', 'Гроб')]
    topic = models.ForeignKey(Topic, on_delete=models.PROTECT, related_name='tasks', verbose_name='Номер')
    title = models.CharField('Название', max_length=200)
    statement = models.TextField('Текст условия (необязательно)', blank=True)
    answer = models.CharField('Правильный ответ', max_length=250, help_text='Несколько чисел разделяйте пробелами. Порядок важен.')
    code_mode = models.CharField('Python-код в ответе ученика', choices=CODE_MODES, default='off', max_length=10, help_text=CODE_HELP)
    solution = models.TextField('Разбор', blank=True)
    difficulty = models.CharField('Сложность', choices=LEVELS, default='easy', max_length=10)
    source = models.CharField('Источник / автор', max_length=250, default='Авторская учебная задача')
    source_system = models.CharField('Банк-источник', max_length=32, blank=True, default='', editable=False)
    external_id = models.PositiveIntegerField('ID в исходном банке', null=True, blank=True, editable=False)
    source_url = models.URLField('Ссылка на оригинал', max_length=500, blank=True, default='', editable=False)
    # FIPI open bank: the task's GUID for FIPI's own answer check (learning/fipi_check.py).
    check_ref = models.CharField('Код проверки ФИПИ', max_length=64, blank=True, default='', editable=False)
    # As on KOMPEGE: a note above the task ("Демоверсия 2027", "Авторская · Л. Шастин") and its kind for filtering.
    source_label = models.CharField('Пометка источника', max_length=120, blank=True, default='')
    source_kind = models.CharField('Тип задания', max_length=10, blank=True, default='',
        choices=[('demo', 'Демоверсия'), ('exam', 'Реальный ЕГЭ'), ('probe', 'Пробники и ЕГКР'), ('author', 'Авторские')])
    # Where an exam task comes from (learning/exam_sources.py); the official ones form the «ЕГЭ» level.
    EXAM_SOURCES = [('demo', 'Демоверсия'), ('approbation', 'Апробация'), ('early', 'Досрочная волна'), ('main', 'Основная волна'),
                    ('exam', 'Реальный ЕГЭ'), ('reserve', 'Резерв'), ('retake', 'Пересдача'), ('egkr', 'ЕГКР'),
                    ('open', 'Открытый вариант'), ('fipi', 'Открытый банк ФИПИ'), ('statgrad', 'Статград')]
    exam_source = models.CharField('Источник ЕГЭ', max_length=12, blank=True, default='', db_index=True, choices=EXAM_SOURCES)
    exam_source_manual = models.BooleanField('Тип выбран преподавателем', default=False)
    original_difficulty = models.CharField('Сложность до уровня «ЕГЭ»', max_length=10, blank=True, default='')
    exam_year = models.PositiveSmallIntegerField('Год выпуска', null=True, blank=True, db_index=True,
        help_text='Год экзамена или сборника из пометки источника («ЕГЭ-2025», «Апробация 04.03.26»).')
    attachment = models.FileField('Данные к задаче', upload_to='tasks/', blank=True, validators=[file_size, FileExtensionValidator(['txt', 'csv', 'xlsx', 'pdf', 'zip'])])
    published = models.BooleanField('Опубликовано', default=True)
    # Tasks of imported KOMPEGE variants that the source keeps out of its public bank.
    in_catalog = models.BooleanField('Показывать в каталоге', default=True, help_text='Выключено — задание доступно только внутри вариантов.')
    created_at = models.DateTimeField(auto_now_add=True)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    tags = models.CharField('Теги через запятую', max_length=300, blank=True)
    subtopic = models.CharField('Подтема', max_length=160, blank=True)
    class Meta:
        ordering = ['topic__number', 'id']
        indexes = [models.Index(fields=['published', 'topic', 'difficulty'])]
        constraints = [models.UniqueConstraint(fields=['source_system', 'external_id', 'topic'], condition=models.Q(external_id__isnull=False), name='unique_task_source_id_topic')]
        verbose_name = 'задание'
        verbose_name_plural = 'задания'
    def __str__(self):
        return f'#{self.pk} · №{self.topic.number} · {self.title}'

    @property
    def polyakov_ref(self):
        from .sources import polyakov_ref
        return polyakov_ref(self.topic.number, self.external_id)


class Practice(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    answer = models.CharField(max_length=250)
    python_code = models.TextField('Python-код ученика', blank=True, max_length=MAX_CODE_LENGTH)
    correct = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'created_at']), models.Index(fields=['user', 'task', 'id'])]


class Bookmark(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'task'], name='unique_bookmark')]


class StudyGroup(models.Model):
    title = models.CharField('Название группы', max_length=120)
    students = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, verbose_name='Ученики')
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='teaching_groups', verbose_name='Преподаватель')
    class Meta:
        verbose_name = 'группа'
        verbose_name_plural = 'группы'
    def __str__(self):
        return self.title


class Variant(models.Model):
    objects = SubjectManager()
    all_objects = models.Manager()
    subject = subject_field()
    title = models.CharField('Название', max_length=180)
    kind = models.CharField('Тип', max_length=10, choices=[('exam', 'Вариант'), ('test', 'Контрольная')], default='exam')
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    topics = models.ManyToManyField(Topic, blank=True)
    group = models.ForeignKey(StudyGroup, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Для группы', help_text='Без группы — для всех учеников.')
    duration = models.PositiveSmallIntegerField('Время, мин', default=235, validators=[MinValueValidator(1), MaxValueValidator(600)])
    published = models.BooleanField('Опубликован', default=False)
    assigned_only = models.BooleanField(default=False, editable=False)
    # Imported KOMPEGE variants keep their source labels and are scored like the exam.
    SCORING = [('plain', 'Число верных ответов'), ('ege', 'Первичные и тестовые баллы ЕГЭ')]
    scoring = models.CharField('Подсчёт результата', max_length=10, choices=SCORING, default='plain')
    hide_answers = models.BooleanField('Скрывать верные ответы после сдачи', default=False)
    school_year = models.CharField('Учебный год', max_length=9, blank=True)
    collection = models.CharField('Сборник', max_length=150, blank=True)
    source_system = models.CharField(max_length=32, blank=True, default='', editable=False)
    external_id = models.PositiveIntegerField('Номер КИМ в источнике', null=True, blank=True, editable=False)
    source_url = models.URLField(max_length=500, blank=True, default='', editable=False)
    sort_order = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['source_system', 'external_id'], condition=models.Q(external_id__isnull=False), name='unique_variant_source_id')]
        verbose_name = 'вариант / контрольная'
        verbose_name_plural = 'варианты и контрольные'
    def __str__(self):
        return self.title


class VariantItem(models.Model):
    variant = models.ForeignKey(Variant, on_delete=models.CASCADE, related_name='items')
    task = models.ForeignKey(Task, on_delete=models.PROTECT)
    position = models.PositiveSmallIntegerField()
    # Snapshot protects issued variants against later edits to the task bank.
    number = models.PositiveSmallIntegerField()
    statement = models.TextField()
    answer = models.CharField(max_length=250)
    code_mode = models.CharField(choices=CODE_MODES, default='off', max_length=10)
    solution = models.TextField()
    attachment = models.CharField(max_length=500, blank=True)
    blocks = models.JSONField(default=list, blank=True)
    # KEGE-style answer grid; answers are stored as cells joined by spaces, rows by newlines.
    answer_rows = models.PositiveSmallIntegerField(default=1)
    answer_cols = models.PositiveSmallIntegerField(default=1)
    class Meta:
        ordering = ['position']
        constraints = [models.UniqueConstraint(fields=['variant', 'position'], name='unique_variant_position')]


class ExamAttempt(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT)
    started_at = models.DateTimeField(auto_now_add=True)
    deadline = models.DateTimeField()
    submitted_at = models.DateTimeField(null=True, blank=True)
    score = models.PositiveSmallIntegerField(default=0)
    primary_score = models.PositiveSmallIntegerField(null=True, blank=True)
    test_score = models.PositiveSmallIntegerField(null=True, blank=True)
    class Meta:
        ordering = ['-started_at']
        indexes = [models.Index(fields=['user', 'submitted_at']), models.Index(fields=['submitted_at', 'deadline'])]
        constraints = [models.UniqueConstraint(fields=['user', 'variant'], condition=models.Q(submitted_at__isnull=True), name='one_active_attempt')]


class ExamAnswer(models.Model):
    attempt = models.ForeignKey(ExamAttempt, on_delete=models.CASCADE, related_name='answers')
    item = models.ForeignKey(VariantItem, on_delete=models.PROTECT)
    answer = models.CharField(max_length=250, blank=True)
    python_code = models.TextField('Python-код ученика', blank=True, max_length=MAX_CODE_LENGTH)
    comment = models.TextField('Комментарий ученика', blank=True, max_length=3000)
    correct = models.BooleanField(default=False)
    points = models.PositiveSmallIntegerField(default=0)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['attempt', 'item'], name='unique_exam_answer')]


class Section(models.Model):
    objects = ActiveManager()
    all_objects = models.Manager()
    course = models.ForeignKey('Course', on_delete=models.CASCADE, related_name='sections', null=True, blank=True, verbose_name='Курс')
    title = models.CharField('Раздел', max_length=180)
    description = models.TextField('Описание', blank=True)
    order = models.PositiveSmallIntegerField(default=1)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    class Meta:
        ordering = ['order', 'id']
        verbose_name = 'раздел курса'
        verbose_name_plural = 'разделы курса'
    def __str__(self):
        return self.title


class TaskBlock(models.Model):
    objects = ActiveManager()
    all_objects = models.Manager()
    # 'html': formatted text with tables (FIPI conditions), shown through the safe HTML filter.
    KINDS = [('text', 'Текст'), ('heading', 'Заголовок'), ('image', 'Изображение'), ('file', 'Файл'), ('code', 'Python-код'), ('html', 'Текст с таблицами')]
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='content_blocks')
    purpose = models.CharField('Часть задания', max_length=12, choices=[('statement', 'Условие'), ('solution', 'Разбор')], default='statement')
    kind = models.CharField('Тип блока', max_length=12, choices=KINDS)
    position = models.PositiveIntegerField('Порядок', default=1)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    text = models.TextField('Текст / подпись', blank=True)
    image = models.ImageField('Изображение', upload_to='task-images/', blank=True, validators=[file_size, FileExtensionValidator(['png', 'jpg', 'jpeg', 'webp'])])
    file = models.FileField('Файл', upload_to='task-blocks/', blank=True, validators=[data_file_size, FileExtensionValidator(['txt', 'csv', 'xlsx', 'xls', 'pdf', 'zip', 'docx', 'doc', 'ods', 'odt', 'py'])])
    class Meta:
        ordering = ['position', 'id']
        verbose_name = 'блок задания'
        verbose_name_plural = 'блоки задания'
    def clean(self):
        if self.kind in ('text', 'heading', 'code', 'html') and not self.text.strip():
            raise ValidationError({'text': 'Введите текст блока.'})
        if self.kind == 'image' and not self.image:
            raise ValidationError({'image': 'Добавьте изображение.'})
        if self.kind == 'file' and not self.file:
            raise ValidationError({'file': 'Прикрепите файл.'})
    def snapshot(self):
        return {'purpose': self.purpose, 'kind': self.kind, 'text': self.text, 'image': self.image.name, 'file': self.file.name}


class StudentAssignment(models.Model):
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='assigned_students', limit_choices_to={'is_staff': True}, verbose_name='Преподаватель')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='assigned_teachers', limit_choices_to={'is_staff': False}, verbose_name='Ученик')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['teacher', 'student'], name='unique_student_assignment')]
        verbose_name = 'закрепление ученика'
        verbose_name_plural = 'закреплённые ученики'
    def clean(self):
        if self.student_id and self.student.is_staff:
            raise ValidationError('Выберите ученика, а не преподавателя.')
        if self.teacher_id and not self.teacher.is_staff:
            raise ValidationError('Преподаватель должен иметь статус персонала.')


class Course(models.Model):
    objects = ActiveManager()
    all_objects = models.Manager()
    subject = subject_field()
    title = models.CharField('Название курса', max_length=180)
    description = models.TextField('Описание', blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='authored_courses', verbose_name='Автор')
    cover = models.ImageField('Обложка', upload_to='course-covers/', blank=True, validators=[file_size, FileExtensionValidator(['png', 'jpg', 'jpeg', 'webp'])])
    published = models.BooleanField('Опубликовать курс', default=False)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    sequential = models.BooleanField('Проходить шаги по порядку', default=False)
    group = models.ForeignKey(StudyGroup, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Доступ для группы', help_text='Пусто — все зарегистрированные ученики.')
    class Meta:
        verbose_name = 'курс'
        verbose_name_plural = 'курсы'
    def __str__(self):
        return self.title


class Step(models.Model):
    objects = ActiveManager()
    all_objects = models.Manager()
    KINDS = [('text', 'Текст и иллюстрация'), ('video', 'Видео'), ('quiz', 'Выбор ответа'), ('answer', 'Короткий ответ'), ('numeric', 'Числовой ответ'), ('gaps', 'Заполнение пропусков'), ('matching', 'Сопоставление пар'), ('file', 'Файл'), ('notebook', 'Конспект'), ('task', 'Задача из банка')]
    lesson = models.ForeignKey('Lesson', on_delete=models.CASCADE, related_name='steps')
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.PROTECT, related_name='course_steps', verbose_name='Задача из банка', help_text='Условие, файлы и проверка берутся из каталога; ответ засчитывается и в каталоге.')
    title = models.CharField('Название шага', max_length=180)
    kind = models.CharField('Тип шага', max_length=12, choices=KINDS, default='text')
    position = models.PositiveIntegerField('Порядок', default=1)
    body = models.TextField('Текст / условие', blank=True)
    code = models.TextField('Python-код', blank=True, help_text='Показывается ученику с сохранением отступов. Не выполняется на сервере.')
    quiz_mode = models.CharField('Выбор ответов', max_length=10, default='auto', choices=[('auto', 'Автоматически'), ('single', 'Один ответ'), ('multiple', 'Несколько ответов')])
    image = models.ImageField('Иллюстрация', upload_to='step-images/', blank=True, validators=[file_size, FileExtensionValidator(['png', 'jpg', 'jpeg', 'webp'])])
    video = models.FileField('Видео MP4 / WebM', upload_to='step-videos/', blank=True, validators=[video_size, FileExtensionValidator(['mp4', 'webm'])])
    file = models.FileField('Материал', upload_to='step-files/', blank=True, validators=[file_size, FileExtensionValidator(['txt', 'csv', 'xlsx', 'pdf', 'zip', 'docx', 'py'])])
    options = models.JSONField('Варианты', default=list, blank=True)
    correct_choices = models.JSONField('Номера верных вариантов', default=list, blank=True)
    answer = models.CharField('Правильный ответ', max_length=250, blank=True)
    code_mode = models.CharField('Python-код в ответе ученика', choices=CODE_MODES, default='off', max_length=10, help_text=CODE_HELP)
    explanation = models.TextField('Разбор после ответа', blank=True)
    points = models.PositiveSmallIntegerField('Баллы', default=1, validators=[MaxValueValidator(100)])
    max_attempts = models.PositiveSmallIntegerField('Лимит попыток (0 — без ограничений)', default=0, validators=[MaxValueValidator(100)])
    published = models.BooleanField('Опубликован', default=True)
    revision = models.PositiveIntegerField(default=1, editable=False)
    edit_version = models.PositiveIntegerField(default=1, editable=False)
    use_blocks = models.BooleanField(default=False, editable=False)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    tolerance = models.DecimalField('Допустимая погрешность', max_digits=18, decimal_places=8, default=0, validators=[MinValueValidator(0)])
    pass_percent = models.PositiveSmallIntegerField('Проходной результат, %', default=100, validators=[MinValueValidator(1), MaxValueValidator(100)])
    pairs = models.JSONField('Пропуски / пары', default=list, blank=True)
    class Meta:
        ordering = ['position', 'id']
        verbose_name = 'шаг'
        verbose_name_plural = 'шаги'
    def clean(self):
        from decimal import Decimal, InvalidOperation
        if self.published and self.kind == 'numeric':
            try:
                if not Decimal(self.answer.replace(',', '.')).is_finite():
                    raise InvalidOperation
            except InvalidOperation:
                raise ValidationError({'answer': 'Введите конечное число.'})
        if self.published and self.kind in ('gaps', 'matching'):
            if not isinstance(self.pairs, list) or not self.pairs or len(self.pairs) > 30 or any(not isinstance(p, dict) or not str(p.get('prompt', '')).strip() or not str(p.get('answer', '')).strip() for p in self.pairs):
                raise ValidationError('Добавьте от 1 до 30 пар «подсказка = ответ».')
        if self.kind == 'quiz' and self.published:
            if not isinstance(self.options, list) or len(self.options) < 2 or not all(isinstance(v, str) and v.strip() for v in self.options):
                raise ValidationError('Для теста нужны минимум два текстовых варианта.')
            if not isinstance(self.correct_choices, list) or not self.correct_choices or any(type(n) is not int or n < 0 or n >= len(self.options) for n in self.correct_choices):
                raise ValidationError('Укажите корректные номера правильных ответов.')
            if self.quiz_mode == 'single' and len(set(self.correct_choices)) != 1:
                raise ValidationError('В тесте с одним ответом отметьте ровно один правильный вариант.')
        if self.kind == 'task' and self.published and not (self.task_id and self.task.published):
            raise ValidationError({'task': 'Выберите опубликованную задачу из банка.'})
        if self.kind == 'answer' and self.published and not self.answer.strip():
            raise ValidationError({'answer': 'Укажите правильный ответ.'})
        if self.use_blocks:
            kinds = getattr(self, '_content_kinds', None)
            if kinds is None:
                kinds = list(self.content_blocks.values_list('kind', flat=True)) if self.pk else []
            if self.published and self.kind == 'video' and 'video' not in kinds:
                raise ValidationError('Добавьте видеоблок перед публикацией.')
            if self.published and self.kind == 'file' and 'file' not in kinds:
                raise ValidationError('Добавьте блок с файлом перед публикацией.')
            if self.published and self.kind == 'text' and not kinds:
                raise ValidationError('Добавьте хотя бы один блок материала.')
            if self.published and self.kind == 'notebook' and 'notebook' not in kinds:
                raise ValidationError('Выберите конспект перед публикацией слайда.')
            if self.published:
                notebooks = getattr(self, '_content_notebooks', None)
                if notebooks is None:
                    notebooks = list(self.content_blocks.filter(kind='notebook').select_related('notebook')) if self.pk else []
                    notebooks = [block.notebook for block in notebooks]
                if any(not book or not book.published or book.archived_at for book in notebooks):
                    raise ValidationError('Опубликуйте выбранные конспекты перед публикацией слайда.')
            return
        if self.published and self.kind == 'notebook':
            raise ValidationError('Добавьте блок с конспектом через редактор слайда.')
        if self.kind == 'video' and self.published and not self.video:
            raise ValidationError({'video': 'Для публикации видеошага загрузите видео.'})
        if self.kind == 'file' and self.published and not self.file:
            raise ValidationError({'file': 'Для публикации шага загрузите файл.'})
        if self.kind == 'text' and self.published and not (self.body.strip() or self.image or self.code.strip()):
            raise ValidationError({'body': 'Добавьте текст или иллюстрацию.'})
    def __str__(self):
        return self.title


class StepContentBlock(models.Model):
    KINDS = [('text', 'Текст'), ('image', 'Фото'), ('video', 'Видео'), ('code', 'Python-код'), ('file', 'Файл'), ('divider', 'Разделитель'), ('notebook', 'Конспект')]
    step = models.ForeignKey(Step, on_delete=models.CASCADE, related_name='content_blocks')
    notebook = models.ForeignKey('learning.Notebook', null=True, blank=True, on_delete=models.PROTECT, related_name='course_blocks', verbose_name='Конспект')
    kind = models.CharField('Блок', choices=KINDS, max_length=12, default='text')
    position = models.PositiveIntegerField('Порядок', default=1)
    text = models.TextField('Текст', blank=True)
    caption = models.CharField('Подпись / описание изображения', blank=True, max_length=300)
    image = models.ImageField('Фото', upload_to='course-blocks/images/', blank=True, validators=[file_size, FileExtensionValidator(['png', 'jpg', 'jpeg', 'webp'])])
    video = models.FileField('Видео', upload_to='course-blocks/videos/', blank=True, validators=[video_size, FileExtensionValidator(['mp4', 'webm'])])
    file = models.FileField('Файл', upload_to='course-blocks/files/', blank=True, validators=[file_size, FileExtensionValidator(['txt', 'csv', 'xlsx', 'pdf', 'zip', 'docx', 'py'])])

    class Meta:
        ordering = ['position', 'id']
        verbose_name = 'блок шага'
        verbose_name_plural = 'блоки шага'

    def clean(self):
        from django.utils.html import strip_tags
        from .rich_text import sanitize_html
        if self.kind == 'text':
            self.text = sanitize_html(self.text)
            if not strip_tags(self.text).strip():
                raise ValidationError({'text': 'Напишите текст блока.'})
        if self.kind == 'code' and not self.text.strip():
            raise ValidationError({'text': 'Добавьте код.'})
        if self.kind in ('image', 'video', 'file') and not getattr(self, self.kind):
            raise ValidationError({self.kind: 'Загрузите материал или выберите его из медиатеки.'})
        if self.kind == 'notebook' and not self.notebook_id:
            raise ValidationError({'notebook': 'Выберите конспект.'})

    def save(self, *args, **kwargs):
        if self.kind == 'text':
            from .rich_text import sanitize_html
            self.text = sanitize_html(self.text)
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.get_kind_display()} · шаг {self.step_id}'


class Enrollment(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='enrollments')
    joined_at = models.DateTimeField(auto_now_add=True)
    last_step = models.ForeignKey(Step, null=True, blank=True, on_delete=models.SET_NULL)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'course'], name='unique_enrollment')]


class StepProgress(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    step = models.ForeignKey(Step, on_delete=models.CASCADE)
    revision = models.PositiveIntegerField(default=1)
    completed = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    points = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'step'], name='unique_step_progress')]


class StepSubmission(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    step = models.ForeignKey(Step, on_delete=models.CASCADE)
    revision = models.PositiveIntegerField(default=1)
    answer = models.TextField()
    python_code = models.TextField('Python-код ученика', blank=True, max_length=MAX_CODE_LENGTH)
    correct = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['user', 'created_at'])]


class StepComment(models.Model):
    step = models.ForeignKey(Step, on_delete=models.CASCADE, related_name='comments')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    text = models.TextField('Комментарий', max_length=3000)
    created_at = models.DateTimeField(auto_now_add=True)
    hidden = models.BooleanField(default=False)
    class Meta:
        ordering = ['created_at', 'id']


class Lesson(models.Model):
    objects = ActiveManager()
    all_objects = models.Manager()
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='lessons', verbose_name='Раздел')
    title = models.CharField('Название урока', max_length=180)
    order = models.PositiveSmallIntegerField(default=1)
    minutes = models.PositiveSmallIntegerField('Длительность, мин', default=20)
    video = models.FileField('Видеозапись', upload_to='videos/', blank=True, validators=[video_size, FileExtensionValidator(['mp4', 'webm'])])
    theory = models.TextField('Теория')
    published = models.BooleanField('Опубликован', default=False)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    class Meta:
        ordering = ['section__order', 'order', 'id']
        verbose_name = 'урок'
        verbose_name_plural = 'уроки'
    def __str__(self):
        return self.title


class QuizQuestion(models.Model):
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name='questions')
    text = models.TextField('Вопрос')
    options = models.JSONField('Варианты ответа', default=list, help_text='JSON-массив строк: ["Ответ 1", "Ответ 2"]')
    correct_index = models.PositiveSmallIntegerField('Индекс верного ответа (с 0)', default=0)
    explanation = models.TextField('Объяснение')
    def clean(self):
        if not isinstance(self.options, list) or len(self.options) < 2 or not all(isinstance(x, str) and x.strip() for x in self.options):
            raise ValidationError('Нужны минимум два текстовых варианта ответа.')
        if self.correct_index >= len(self.options):
            raise ValidationError('Индекс выходит за число вариантов.')


class LessonProgress(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE)
    passed = models.BooleanField(default=False)
    score = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'lesson'], name='unique_progress')]


class LessonFile(models.Model):
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name='files')
    title = models.CharField('Название', max_length=180)
    file = models.FileField('Файл', upload_to='lessons/', validators=[file_size, FileExtensionValidator(['txt', 'pdf', 'csv', 'xlsx', 'zip'])])
    def __str__(self):
        return self.title


# Import extension models so Django registers their migrations and relations.
from .platform_models import (LoginBucket, StudentAccess, ActionReceipt, TeacherNote, LearningAssignment, AssignmentRecipient, HomeworkSubmission, Notification, GroupInvite, InviteUse, EditorDraft, VideoPosition, MediaAsset, AuditEvent, TrashEntry, ImportBatch)
from .notebook_models import Notebook, NotebookCell
from .schedule_models import ScheduleEvent, ScheduleOccurrence, ScheduleReminder
from .collection_models import Collection, CollectionItem
from .similar_models import TaskSimilarity, TaskVector
from .coverage_models import GroupTopicMark
