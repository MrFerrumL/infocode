from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings


class PublicPagesTests(TestCase):
    def test_guest_sees_landing_without_password_form(self):
        page = self.client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, 'public/home.html')
        self.assertContains(page, 'Подготовка к ЕГЭ')
        self.assertContains(page, 'href="/login/"')
        self.assertContains(page, 'href="/contacts/"')
        self.assertNotContains(page, 'type="password"')

    def test_signed_in_user_still_gets_dashboard(self):
        user = get_user_model().objects.create_user('pupil', password='long-test-pass-42')
        self.client.force_login(user)
        page = self.client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, 'dashboard.html')

    def test_private_pages_still_require_login(self):
        self.assertRedirects(self.client.get('/catalog/'), '/login/?next=/catalog/', fetch_redirect_response=False)

    @override_settings(SITE_OWNER_NAME='Тестовый Преподаватель', SITE_CONTACT_EMAIL='teacher@example.ru', SITE_CONTACT_TELEGRAM='@teacher_test')
    def test_contacts_show_configured_details(self):
        page = self.client.get('/contacts/')
        self.assertContains(page, 'Тестовый Преподаватель')
        self.assertContains(page, 'mailto:teacher@example.ru')
        self.assertContains(page, 'https://t.me/teacher_test')

    @override_settings(SITE_OWNER_NAME='', SITE_CONTACT_EMAIL='', SITE_CONTACT_TELEGRAM='')
    def test_contacts_without_details_point_to_teacher(self):
        page = self.client.get('/contacts/')
        self.assertContains(page, 'через своего преподавателя')
        self.assertNotContains(page, 'mailto:')

    def test_robots_allows_public_pages_only(self):
        page = self.client.get('/robots.txt')
        self.assertEqual(page['Content-Type'], 'text/plain; charset=utf-8')
        body = page.content.decode()
        self.assertIn('Allow: /contacts/', body)
        self.assertTrue(body.rstrip().endswith('Disallow: /'))

    @override_settings(GOOGLE_SITE_VERIFICATION='abc123-token')
    def test_landing_carries_search_console_tag_when_configured(self):
        self.assertContains(self.client.get('/'), '<meta name="google-site-verification" content="abc123-token">')
        with self.settings(GOOGLE_SITE_VERIFICATION=''):
            self.assertNotContains(self.client.get('/'), 'google-site-verification')

    def test_privacy_policy_is_public_and_linked_from_the_entry_pages(self):
        page = self.client.get('/privacy/')
        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, 'public/privacy.html')
        self.assertContains(page, 'Политика конфиденциальности')
        self.assertContains(page, 'ФИПИ')
        self.assertNotContains(page, 'type="password"')
        self.assertIn('Allow: /privacy/', self.client.get('/robots.txt').content.decode())
        for url in ('/', '/contacts/', '/login/', '/register/'):
            self.assertContains(self.client.get(url), 'href="/privacy/"', msg_prefix=url)

    @override_settings(SITE_OWNER_NAME='Тестовый Преподаватель')
    def test_privacy_names_the_owner_only_when_configured(self):
        self.assertContains(self.client.get('/privacy/'), 'Тестовый Преподаватель')
        with self.settings(SITE_OWNER_NAME=''):
            self.assertNotContains(self.client.get('/privacy/'), 'Владелец и преподаватель')

    def test_login_page_says_what_it_asks_for(self):
        page = self.client.get('/login/')
        self.assertContains(page, 'Инфокод — закрытая учебная платформа преподавателя')
        self.assertContains(page, 'href="/contacts/"')
