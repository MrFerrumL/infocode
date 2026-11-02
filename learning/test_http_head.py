import io

from django.core.handlers.wsgi import WSGIHandler
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.middleware.common import CommonMiddleware
from django.test import RequestFactory, SimpleTestCase

from .middleware import HeadResponseMiddleware


class HeadResponseTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_head_keeps_get_headers_without_body(self):
        response = HttpResponse('page content', headers={'ETag': '"revision-1"'})
        handler = HeadResponseMiddleware(CommonMiddleware(lambda request: response))
        result = handler(self.factory.head('/'))
        self.assertEqual(result.content, b'')
        self.assertEqual(result['Content-Length'], '12')
        self.assertEqual(result['ETag'], '"revision-1"')

    def test_get_is_unchanged(self):
        result = HeadResponseMiddleware(lambda request: HttpResponse('page content'))(self.factory.get('/'))
        self.assertEqual(result.content, b'page content')

    def test_head_does_not_consume_stream_and_still_closes_it(self):
        class Stream:
            closed = False
            def __iter__(self):
                return self
            def __next__(self):
                raise AssertionError('HEAD must not read the body')
            def close(self):
                self.closed = True
        # Use an iterator whose construction is harmless but consumption fails.
        stream = Stream()
        response = StreamingHttpResponse(stream, headers={'Content-Length': '999'})
        result = HeadResponseMiddleware(lambda request: response)(self.factory.head('/'))
        self.assertEqual(list(result.streaming_content), [])
        self.assertEqual(result['Content-Length'], '999')
        result.close()
        self.assertTrue(stream.closed)

    def test_head_file_does_not_escape_through_wsgi_file_wrapper(self):
        source = io.BytesIO(b'file content')
        response = FileResponse(source)
        result = HeadResponseMiddleware(lambda request: response)(self.factory.head('/download/'))
        self.assertIsNone(result.file_to_stream)
        self.assertEqual(list(result.streaming_content), [])
        self.assertEqual(result['Content-Length'], '12')
        result.close()
        self.assertTrue(source.closed)

    def test_real_wsgi_handler_returns_no_html_for_head(self):
        # Django's test client strips HEAD bodies itself and would hide this bug.
        environ = self.factory.head('/login/').environ
        statuses = []
        response = WSGIHandler()(environ, lambda status, headers: statuses.append((status, dict(headers))))
        try:
            self.assertEqual(statuses[0][0], '200 OK')
            self.assertGreater(int(statuses[0][1]['Content-Length']), 0)
            self.assertEqual(b''.join(response), b'')
        finally:
            response.close()
