import os
import shutil
import time
import datetime
import socket

from django.conf import settings
from django.contrib.sites.models import Site
from django.test.client import Client as TestClient

from mock import patch
from nose import SkipTest
from nose.tools import eq_
from pyquery import PyQuery as pq
import test_utils

from input.urlresolvers import reverse
from search.client import Client, SearchError
from search.utils import start_sphinx, stop_sphinx, reindex


# TODO(davedash): liberate from Zamboni
class SphinxTestCase(test_utils.TransactionTestCase):
    """
    This test case type can setUp and tearDown the sphinx daemon.  Use this
    when testing any feature that requires sphinx.
    """

    fixtures = ['feedback/opinions']
    sphinx = True
    sphinx_is_running = False

    def setUp(self):
        super(SphinxTestCase, self).setUp()

        if not SphinxTestCase.sphinx_is_running:
            if (not settings.SPHINX_SEARCHD or
                not settings.SPHINX_INDEXER):  # pragma: no cover
                raise SkipTest()

            os.environ['DJANGO_ENVIRONMENT'] = 'test'

            # XXX: Path names need to be more clear.
            if os.path.exists(settings.SPHINX_CATALOG_PATH):
                shutil.rmtree(settings.SPHINX_CATALOG_PATH)
            if os.path.exists(settings.SPHINX_LOG_PATH):
                shutil.rmtree(settings.SPHINX_LOG_PATH)

            os.makedirs(settings.SPHINX_LOG_PATH)
            os.makedirs(settings.SPHINX_CATALOG_PATH)

            reindex()
            start_sphinx()
            time.sleep(1)
            SphinxTestCase.sphinx_is_running = True

    @classmethod
    def tearDownClass(cls):
        if SphinxTestCase.sphinx_is_running:
            stop_sphinx()
            SphinxTestCase.sphinx_is_running = False

query = lambda x='', **kwargs: Client().query(x, **kwargs)
num_results = lambda x='', **kwargs: len(query(x, **kwargs))


class SearchTest(SphinxTestCase):

    def test_query(self):
        eq_(num_results(), 28)

    def test_default_ordering(self):
        """Any query should return results in rev-chron order."""
        r = query()
        dates = [o.created for o in r]
        eq_(dates, sorted(dates, reverse=True), "These aren't revchron.")

        r = query('Firefox')
        dates = [o.created for o in r]
        eq_(dates, sorted(dates, reverse=True), "These aren't revchron.")

    def test_product_filter(self):
        eq_(num_results(product=1), 28)
        eq_(num_results(product=2), 0)

    def test_version_filter(self):
        eq_(num_results(version='3.6.3'), 11)
        eq_(num_results(version='3.6.4'), 16)

    def test_positive_filter(self):
        eq_(num_results(positive=1), 17)
        eq_(num_results(positive=0), 11)

    def test_os_filter(self):
        eq_(num_results(os='mac'), 28)
        eq_(num_results(os='palm'), 0)

    def test_locale_filter(self):
        eq_(num_results(locale='en-US'), 26)
        eq_(num_results(locale='de'), 1)
        eq_(num_results(locale='unknown'), 1)

    def test_date_filter(self):
        start = datetime.datetime(2010, 5, 27)
        end = datetime.datetime(2010, 5, 27)
        eq_(num_results(date_start=start, date_end=end), 5)

    @patch('search.client.sphinx.SphinxClient.Query')
    def test_errors(self, sphinx):
        for error in (socket.timeout(), Exception(),):
            sphinx.side_effect = error
            self.assertRaises(SearchError, query)

    @patch('search.client.sphinx.SphinxClient.GetLastError')
    def test_getlasterror(self, sphinx):
        sphinx = lambda: True
        self.assertRaises(SearchError, query)


def search_request(product='firefox', **kwargs):
    kwargs['product'] = product
    return TestClient().get(reverse('search'), kwargs, follow=True)


class SearchViewTest(SphinxTestCase):
    """Tests relating to the search template rendering."""

    def test_pagination_max(self):
        r = search_request(page=700)
        self.failUnlessEqual(r.status_code, 200)

    @patch('search.views._get_results')
    def test_error(self, get_results):
        get_results.side_effect = SearchError()
        r = search_request()
        eq_(r.status_code, 500)

    def test_atom_link(self):
        r = search_request()
        doc = pq(r.content)
        eq_(len(doc('link[type="application/atom+xml"]')), 1)


class FeedTest(SphinxTestCase):
    def test_invalid_form(self):
        # Sunbird is always the wrong product.
        r = self.client.get(reverse('search.feed'), {'search': 'sunbird'},
                            True)
        self.failUnlessEqual(r.status_code, 200)

    def test_title(self):
        r = self.client.get(reverse('search.feed'),
                            {'product': 'firefox', 'q': 'lol'})
        doc = pq(r.content.replace('xmlns', 'xmlnamespace'))
        eq_(doc('title').text(), "Search for 'lol'")

    def test_query(self):
        r = self.client.get(reverse('search.feed'), {'product': 'firefox'})
        doc = pq(r.content.replace('xmlns', 'xmfail'))
        s = Site.objects.all()[0]
        url_base = 'http://%s/' % s.domain
        eq_(doc('entry link').attr['href'], '%s%s' % (url_base, '#29'))

