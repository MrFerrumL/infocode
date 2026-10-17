"""Replace unchanged imported condition images while retaining snapshot files."""
import hashlib
import io
import json
from pathlib import PurePosixPath
import zipfile

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image

from learning.models import TaskBlock


def inspect_images(archive):
    members=archive.infolist()
    if len(members)>400 or len({m.filename for m in members})!=len(members) or sum(m.file_size for m in members)>256*1024*1024:
        raise ValueError('Duplicate or excessive image archive members')
    if archive.getinfo('manifest.json').file_size>1024*1024:
        raise ValueError('Oversized image manifest')
    manifest=json.loads(archive.read('manifest.json'))
    if not isinstance(manifest,dict): raise ValueError('Invalid image manifest')
    assets=manifest.get('assets',[])
    if manifest.get('format')!='infocode-condition-images-v1' or not isinstance(assets,list) or not 1<=len(assets)<=300:
        raise ValueError('Invalid condition image package')
    seen=set(); result=[]
    for asset in assets:
        path=PurePosixPath(asset['path']); old=PurePosixPath(asset['previous_name'])
        if path.is_absolute() or '..' in path.parts or '\\' in asset['path'] or not path.parts or path.parts[0]!='images' or path.suffix.lower() not in ('.jpg','.png'):
            raise ValueError('Unsafe image member path')
        if old.is_absolute() or '..' in old.parts or '\\' in asset['previous_name'] or len(old.parts)!=5 or old.parts[:2]!=('imports','kompege') or not old.parts[2].isdigit():
            raise ValueError('Only imported condition images can be replaced')
        if str(old) in seen: raise ValueError('Duplicate original image')
        seen.add(str(old))
        for key in ('sha256','previous_sha256'):
            if len(asset[key])!=64 or any(c not in '0123456789abcdef' for c in asset[key]): raise ValueError('Invalid checksum')
        member=archive.getinfo(str(path))
        if member.file_size>20*1024*1024 or (member.external_attr>>16)&0o170000==0o120000: raise ValueError('Oversized image or symlink')
        data=archive.read(member)
        if hashlib.sha256(data).hexdigest()!=asset['sha256']: raise ValueError('Image checksum mismatch')
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ('JPEG','PNG') or image.width<1800 or image.height<30 or image.width*image.height>40_000_000:
                raise ValueError('A new native high-resolution image is required')
            image.verify()
        destination=f'imports/kompege/{old.parts[2]}/hq-{asset["sha256"][:12]}/{path.name}'
        if len(destination)>100: raise ValueError('Image storage filename is too long')
        result.append((asset,data,destination))
    if {m.filename for m in members}!={'manifest.json'}|{a['path'] for a,_,_ in result}:
        raise ValueError('Unexpected archive members')
    return result


class Command(BaseCommand):
    help='Refresh original imported screenshots, preserving task IDs, edits, answers, attempts and snapshot files.'

    def add_arguments(self,parser):
        parser.add_argument('path')
        parser.add_argument('--dry-run',action='store_true')

    def handle(self,path,dry_run=False,**options):
        try:
            with zipfile.ZipFile(path) as archive: assets=inspect_images(archive)
        except (OSError,ValueError,KeyError,TypeError,zipfile.BadZipFile,Image.DecompressionBombError) as exc:
            raise CommandError('Image validation failed: '+str(exc)) from exc
        written=[]; updated=0
        try:
            with transaction.atomic():
                pending=[]
                for asset,data,destination in assets:
                    blocks=list(TaskBlock.all_objects.select_for_update().filter(task__source_system='kompege',purpose='statement',kind='image',image=asset['previous_name']).values_list('pk',flat=True))
                    if not blocks: continue
                    with default_storage.open(asset['previous_name'],'rb') as original:
                        if hashlib.sha256(original.read()).hexdigest()!=asset['previous_sha256']:
                            raise ValueError('An original image was edited; refusing to overwrite it')
                    pending.append((blocks,data,destination))
                if dry_run:
                    self.stdout.write(f'Validated {len(assets)} high-resolution images; pending {sum(len(ids) for ids,_,_ in pending)} blocks. No changes.')
                    return
                for ids,data,destination in pending:
                    if default_storage.exists(destination):
                        with default_storage.open(destination,'rb') as existing:
                            if existing.read()!=data: raise ValueError('Destination checksum mismatch')
                    else:
                        actual=default_storage.save(destination,ContentFile(data));written.append(actual)
                        if actual!=destination: raise ValueError('Unexpected image filename collision')
                    updated+=TaskBlock.all_objects.filter(pk__in=ids).update(image=destination)
        except Exception as exc:
            for name in written: default_storage.delete(name)
            raise CommandError('Image refresh rolled back: '+str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f'High-resolution images ready: updated {updated} blocks. Original files and attempt history retained.'))
