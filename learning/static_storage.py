from urllib.parse import unquote, urlsplit
from whitenoise.storage import CompressedManifestStaticFilesStorage


class HashedStaticStorage(CompressedManifestStaticFilesStorage):
    """Static files under names with a content hash (app.1a2b3c4d5e6f.css).

    A browser keeps such a file for a year without asking again, and a new release changes the name, so nobody sees an
    old stylesheet. Only the manifest written by collectstatic decides: a file that is not in it (tests, a checkout
    before collectstatic) keeps its plain name instead of breaking the page."""
    manifest_strict = False

    def stored_name(self, name):
        if self.hash_key(urlsplit(unquote(name)).path.strip()) not in self.hashed_files:
            return name
        return super().stored_name(name)
