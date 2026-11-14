from django.test import SimpleTestCase


class EntryPagesTests(SimpleTestCase):
    def test_home_requires_master_login(self):
        self.assertRedirects(self.client.get("/"), "/login/?next=/", fetch_redirect_response=False)

    def test_old_admin_redirects_to_family_management(self):
        self.assertRedirects(self.client.get("/admin/"), "/families/", fetch_redirect_response=False)

    def test_login_has_csrf(self):
        self.assertContains(self.client.get("/login/"), "csrfmiddlewaretoken")
