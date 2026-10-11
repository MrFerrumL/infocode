import uuid
from django.core import signing
from django.core.exceptions import PermissionDenied
from .models import ActionReceipt


def form_token(user, scope):
    return signing.dumps({'u': user.pk, 's': scope, 't': str(uuid.uuid4())}, salt='answer-once')


def claim_submission(request, scope):
    token = request.POST.get('submit_token')
    if not token:
        # Compatibility for existing integrations. Browser forms always include a signed token.
        return True
    try:
        data = signing.loads(token, salt='answer-once', max_age=43200)
        if data['u'] != request.user.pk or data['s'] != scope:
            raise PermissionDenied
        key = uuid.UUID(data['t'])
    except (signing.BadSignature, KeyError, ValueError, TypeError):
        raise PermissionDenied('Обновите страницу перед отправкой.')
    _, created = ActionReceipt.objects.get_or_create(user=request.user, scope=scope, token=key)
    return created
