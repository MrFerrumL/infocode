"""Search by photo: the picture's text is recognised here and sent back to the page, which searches by meaning."""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from . import ocr, semantic_search


@login_required
@require_POST
def read_photo(request):
    photo = request.FILES.get('photo')
    if not photo:
        return JsonResponse({'error': 'Выберите или вставьте фото задачи.'}, status=400)
    if not ocr.available():
        return JsonResponse({'error': 'Поиск по фото на этом сервере не настроен.'}, status=503)
    if not semantic_search.allowed_to_read_photo(request.user):
        return JsonResponse({'error': 'Слишком много фото подряд — попробуйте через несколько минут.'}, status=429)
    try:
        text = ocr.read(photo.read(ocr.MAX_BYTES + 1))
    except ocr.Unreadable as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except Exception:
        return JsonResponse({'error': 'Не удалось распознать текст на фото.'}, status=400)
    if len(text) < 10:
        return JsonResponse({'error': 'На фото не нашлось текста. Сфотографируйте условие крупнее и ровнее.'}, status=400)
    return JsonResponse({'text': text[:1500]})
