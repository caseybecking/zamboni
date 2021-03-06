from django.conf import settings

import mock
from nose.tools import eq_
from test_utils import RequestFactory

import amo
import amo.tests

from addons.models import Addon
from editors.models import RereviewQueue
from market.models import AddonPremium, Price
from users.models import UserProfile

from mkt.developers import forms, models
from mkt.site.fixtures import fixture


class TestPremiumForm(amo.tests.TestCase):
    # None of the tests in this TC should initiate Solitude calls.
    fixtures = fixture('webapp_337141')

    def setUp(self):
        self.request = RequestFactory()
        self.addon = Addon.objects.get(pk=337141)
        self.price = Price.objects.create(price='0.99')
        self.user = UserProfile.objects.get(email='steamcube@mozilla.com')

        self.kwargs = {
            'request': self.request,
            'addon': self.addon,
            'user': self.user,
        }

    def test_free_to_premium(self):
        self.request.POST = {'toggle-paid': 'paid'}
        form = forms.PremiumForm(data={}, **self.kwargs)
        assert form.is_valid(), form.errors
        form.save()
        eq_(self.addon.premium_type, amo.ADDON_PREMIUM)
        eq_(self.addon.status, amo.STATUS_NULL)

    def test_free_to_premium_pending(self):
        # Pending apps shouldn't get re-reviewed.
        self.addon.update(status=amo.STATUS_PENDING)

        self.request.POST = {'toggle-paid': 'paid'}
        form = forms.PremiumForm(data={}, **self.kwargs)
        assert form.is_valid(), form.errors
        form.save()
        eq_(RereviewQueue.objects.count(), 0)

    def test_premium_to_free(self):
        # Premium to Free is ok for public apps.
        self.make_premium(self.addon)

        self.request.POST = {'toggle-paid': 'free'}
        data = {'price': self.price.pk}
        form = forms.PremiumForm(data=data, **self.kwargs)
        assert form.is_valid(), form.errors
        form.save()
        eq_(RereviewQueue.objects.count(), 0)
        eq_(self.addon.premium_type, amo.ADDON_FREE)
        eq_(self.addon.status, amo.STATUS_PUBLIC)

    def test_update(self):
        self.make_premium(self.addon)
        self.request.POST = {'toggle-paid': 'paid'}
        price = Price.objects.create(price='9.99')
        form = forms.PremiumForm(data={'price': price.pk}, **self.kwargs)
        assert form.is_valid(), form.errors
        form.save()
        eq_(self.addon.premium.price.pk, price.pk)

    def test_update_new_with_acct(self):
        # This was the situation for a new app that was
        # getting linked to an existing bank account.
        self.addon.update(premium_type=amo.ADDON_PREMIUM)
        self.request.POST = {'toggle-paid': 'paid'}
        data = {'price': self.price.pk}
        form = forms.PremiumForm(data=data, **self.kwargs)
        assert form.is_valid(), form.errors
        form.save()
        addon = Addon.objects.get(pk=self.addon.pk)
        assert addon.premium


class TestPaidRereview(amo.tests.TestCase):
    fixtures = fixture('webapp_337141') + ['market/prices']

    def setUp(self):
        self.addon = Addon.objects.get(pk=337141)
        self.addon.update(status=amo.STATUS_NULL,
                          highest_status=amo.STATUS_PUBLIC)
        self.price = Price.objects.filter()[0]
        AddonPremium.objects.create(addon=self.addon, price=self.price)
        self.user = UserProfile.objects.get(email='steamcube@mozilla.com')
        amo.set_user(self.user)
        seller = models.SolitudeSeller.objects.create(
            resource_uri='/path/to/sel', user=self.user)

        self.account = models.PaymentAccount.objects.create(
            user=self.user, uri='asdf', name='test', inactive=False,
            solitude_seller=seller, bango_package_id=123)

        self.kwargs = {
            'addon': self.addon,
            'user': self.user,
        }

    @mock.patch('mkt.developers.models.client')
    def test_rereview(self, client):
        client.get_product.return_value = {'meta': {'total_count': 0}}
        client.post_product.return_value = {'resource_uri': 'gpuri'}
        client.get_product_bango.return_value = {'meta': {'total_count': 0}}
        client.post_product_bango.return_value = {
            'resource_uri': 'bpruri', 'bango_id': 123}

        form = forms.BangoAccountListForm(
            data={'accounts': self.account.pk}, **self.kwargs)
        assert form.is_valid(), form.errors
        form.save()
        eq_(self.addon.status, amo.STATUS_PUBLIC)
        eq_(RereviewQueue.objects.count(), 1)

        form = forms.BangoAccountListForm(None, **self.kwargs)
        assert form.fields['accounts'].empty_label == None

    @mock.patch('mkt.developers.models.client')
    def test_norereview(self, client):
        client.get_product.return_value = {'meta': {'total_count': 0}}
        client.post_product.return_value = {'resource_uri': 'gpuri'}
        client.get_product_bango.return_value = {'meta': {'total_count': 0}}
        client.post_product_bango.return_value = {
            'resource_uri': 'bpruri', 'bango_id': 123}

        self.addon.update(highest_status=amo.STATUS_PENDING)
        form = forms.BangoAccountListForm(
            data={'accounts': self.account.pk}, **self.kwargs)
        assert form.is_valid(), form.errors
        form.save()
        eq_(self.addon.status, amo.STATUS_PENDING)
        eq_(RereviewQueue.objects.count(), 0)


class TestInappConfigForm(amo.tests.TestCase):
    fixtures = fixture('webapp_337141')

    def setUp(self):
        self.addon = Addon.objects.get(pk=337141)

    def submit(self, **params):
        data = {'postback_url': '/p',
                'chargeback_url': '/c',
                'is_https': False}
        data.update(params)
        fm = forms.InappConfigForm(data=data)
        cfg = fm.save(commit=False)
        cfg.addon = self.addon
        cfg.save()
        return cfg

    @mock.patch.object(settings, 'INAPP_REQUIRE_HTTPS', True)
    def test_cannot_override_https(self):
        cfg = self.submit(is_https=False)
        # This should be True because you cannot configure https.
        eq_(cfg.is_https, True)

    @mock.patch.object(settings, 'INAPP_REQUIRE_HTTPS', False)
    def test_can_override_https(self):
        cfg = self.submit(is_https=False)
        eq_(cfg.is_https, False)
