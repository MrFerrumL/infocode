from whitenoise.storage import CompressedManifestStaticFilesStorage


class HashedStaticStorage(CompressedManifestStaticFilesStorage):
    """Static files under names with a content hash (app.1a2b3c4d5e6f.css).

    A browser keeps such a file for a year without asking again, and a new release changes the name, so nobody sees an
    old stylesheet. A file that is not in the manifest (tests, a checkout before collectstatic) keeps its plain name
    instead of breaking the page."""
    manifest_strict = False

    def stored_name(self, name):
        try:
            return super().stored_name(name)
        except ValueError:
            return name
