import unittest
from datetime import datetime,timezone,timedelta
from discovery.engine import collect,link_candidates,e_gift_to_offer,canonical_url,add_verified_prices

EG='<a href="/en/d/egift-card/all/jb-hi-fi-egift-card">JB Hi-Fi eGift Card</a><a href="https://evil.example/discount">Apple eGift Card</a><a href="/en/d/egift-card/all/apple-egift-card">Apple eGift Card</a>'
SYD='<a href="/en/product/taronga-zoo-sydney-general-admission-for-nrma-members">Taronga Zoo Sydney General Admission</a>'
SOURCES=[{'id':'gift','provider':'nrma','kind':'egift_directory','url':'https://benefits.mynrma.com.au/en/things-to-do/egift-cards','max_details':0},{'id':'attractions','provider':'nrma','kind':'attraction_directory','url':'https://benefits.mynrma.com.au/en/sydney/attractions','max_details':0}]
def transport(url):
    if url.endswith('/egift-cards'):return EG,'ok'
    if url.endswith('/attractions'):return SYD,'ok'
    return None,'fixture missing'

class DiscoveryTests(unittest.TestCase):
    def test_host_policy(self):
        self.assertIsNone(canonical_url('http://benefits.mynrma.com.au/fake'))
        self.assertIsNone(canonical_url('https://benefits.mynrma.com.au.evil.test/fake'))
        self.assertIsNone(canonical_url('https://evil.test/bad'))
    def test_reject_injected_links(self):
        self.assertEqual(len(link_candidates(EG,SOURCES[0]['url'],'egift_directory')),2)
    def test_attraction_discovery(self):
        self.assertEqual(link_candidates(SYD,SOURCES[1]['url'],'attraction_directory')[0]['name'],'Taronga Zoo Sydney General Admission')
    def test_inventory_and_dedup(self):
        a,report=collect(SOURCES,[],transport,sleep=0)
        self.assertEqual(len(a['offers']),3)
        self.assertEqual(report['by_provider']['nrma'],3)
        self.assertTrue(all(o['value'] is None for o in a['offers']))
        b,_=collect(SOURCES,a['offers'],transport,sleep=0)
        self.assertEqual(len(b['offers']),3)
    def test_legacy_offer_kept_when_source_unavailable(self):
        a,_=collect(SOURCES,[],transport,sleep=0)
        b,report=collect(SOURCES,a['offers'],lambda url:(None,'restricted (403)'),sleep=0)
        self.assertEqual(len(b['offers']),3)
        self.assertEqual(report['source_counts'].get('restricted (403)'),2)
    def test_prices_not_guessed(self):
        o=e_gift_to_offer({'name':'JB HI-FI','url':'https://benefits.mynrma.com.au/en/d/egift-card/all/jb-hi-fi-egift-card'},SOURCES[0]['url'],datetime.now(timezone.utc).isoformat())
        add_verified_prices(o,'<h1>JB HI-FI eGift Card</h1><p>$100 $200 $500</p>',datetime.now(timezone.utc).isoformat())
        self.assertIsNone(o['value'])
    def test_duplicate_links(self):
        s=EG + EG
        self.assertEqual(len(link_candidates(s,SOURCES[0]['url'],'egift_directory')),2)

if __name__=='__main__':unittest.main()
