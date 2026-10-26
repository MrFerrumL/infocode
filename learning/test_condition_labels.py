import tempfile
from io import BytesIO, StringIO
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image, ImageDraw, ImageFont
from .condition_labels import clean, plan
from .models import Task, TaskBlock, Topic

FONT = ImageFont.load_default(size=34)


def snapshot(lines, height=None):
    """A picture like K. Polyakov's: (x, text) lines 52 px apart, the number hanging at x≈60."""
    image = Image.new('RGB', (1920, height or 30 + 52 * len(lines)), 'white')
    draw = ImageDraw.Draw(image)
    for row, (x, text) in enumerate(lines):
        draw.text((x, 15 + 52 * row), text, fill='black', font=FONT)
    return image


def ink_in(image, box):
    return image.convert('L').crop(box).point(lambda v: 255 if v < 150 else 0).getbbox() is not None


class ConditionLabelTests(SimpleTestCase):
    def test_number_is_erased_and_text_kept(self):
        image = snapshot([(62, '270)  *(Д. Статный) Среди натуральных чисел найдите'), (140, 'все числа, имеющие ровно 7 делителей.')])
        found = plan(image)
        self.assertIsNotNone(found)
        self.assertLess(found.erase[2], 160)
        result = clean(image)
        self.assertFalse(ink_in(result, (0, 0, found.erase[2], 60)))   # the number is gone
        self.assertTrue(ink_in(result, (found.erase[2] + 5, 0, 1920, 60)))  # «*(Д. Статный)…» stays
        self.assertEqual(result.size, image.size)

    def test_tail_of_the_previous_task_is_cut(self):
        image = snapshot([(140, 'в системе счисления с основанием 4.'), (140, 'Сколько различных значений S?'),
                          (62, '336)  (Е. Джобс) Значение выражения записали'), (140, 'в системе счисления с основанием 5.')])
        found = plan(image)
        self.assertEqual(found.above, 2)
        result = clean(image)
        self.assertLess(result.size[1], image.size[1] - 90)

    def test_series_intro_above_stays_and_plain_text_is_left_alone(self):
        intro = snapshot([(25, '(Е. Джобс) В следующей серии задач используется файл'), (25, 'с фрагментом базы данных.'),
                          (62, '74)  Используя информацию из базы данных, определите'), (140, 'общую стоимость крупы.')])
        self.assertEqual(plan(intro).above, 0)
        self.assertEqual(clean(intro).size, intro.size)
        self.assertIsNone(plan(snapshot([(140, 'Текст без номера в начале строки'), (140, 'и ещё одна строка')])))


class CleanCommandTests(TestCase):
    """The command cleans the first picture of every Polyakov task, keeps the original and can put it back."""

    def test_clean_rerun_and_revert(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            topic = Topic.objects.create(number=14, title='Системы счисления')
            task = Task.objects.create(topic=topic, title='К. Поляков 14.336', answer='5', source_system='polyakov', external_id=336)
            buffer = BytesIO()
            snapshot([(140, 'Сколько различных значений S?'), (62, '336)  (Е. Джобс) Значение выражения'),
                      (140, 'в системе счисления с основанием 5.')]).save(buffer, 'JPEG')
            from django.core.files.storage import default_storage
            name = default_storage.save('imports/polyakov/336/x/14-336-condition.jpg', ContentFile(buffer.getvalue()))
            block = TaskBlock.objects.create(task=task, kind='image', purpose='statement', position=1, image=name)
            out = StringIO()
            call_command('clean_polyakov_numbers', '--dry-run', stdout=out)
            self.assertIn('cleaned: 1', out.getvalue())
            call_command('clean_polyakov_numbers', stdout=StringIO())
            block.refresh_from_db()
            self.assertTrue(block.image.name.startswith('imports/polyakov/336/x/14-336-condition-clean'))
            with Image.open(block.image.path) as cleaned:
                self.assertLess(cleaned.size[1], 3 * 52)
            self.assertEqual(Task.objects.get().external_id, 336)  # the author's number stays for sorting
            out = StringIO()
            call_command('clean_polyakov_numbers', stdout=out)
            self.assertIn('already: 1', out.getvalue())
            call_command('clean_polyakov_numbers', '--revert', stdout=StringIO())
            block.refresh_from_db()
            self.assertEqual(block.image.name, 'imports/polyakov/336/x/14-336-condition.jpg')
