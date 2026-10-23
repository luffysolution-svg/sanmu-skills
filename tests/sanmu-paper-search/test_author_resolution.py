"""Identity decisions must distinguish ambiguity, absence and source failure."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"skills"/"sanmu-paper-search"/"scripts"))
from _core import ids, record
from _providers import Client, Providers, SourceError
from paper_search import main


class AuthorResolutionTests(unittest.TestCase):
    def run_case(self, query, authors=None, failed=False):
        called = []
        def lookup(adapter, source, identifier):
            if failed: raise SourceError("source unavailable", 503)
            return [record(source,"seed","Seed",2024,ids(doi="10.1234/seed"),authors or [])], {
                "status":"ok","fetched":1,"pages":1,"truncated":False}
        def works(adapter, provider, query, author_id, limit, *args):
            called.append(author_id)
            return [], {"status":"ok","fetched":0,"pages":1,"truncated":False}, []
        with tempfile.TemporaryDirectory() as tmp:
            argv=["author","--provider","semantic-scholar","--from-paper","10.1234/seed","--out",str(Path(tmp)/"run")]
            if query is not None: argv.append(query)
            output=io.StringIO()
            with patch.object(Providers,"lookup",lookup), patch.object(Providers,"authors",works), contextlib.redirect_stdout(output):
                code=main(argv,Client({}))
            return code,json.loads(output.getvalue()),called

    def test_multiple_ids_are_candidates_not_selected(self):
        for names,query in ((["Wei Li","Ming Li"],"Li"),(["Wei Li","Wei Li"],"Wei Li")):
            authors=[{"name":name,"identifiers":ids(semantic_scholar=str(i+101))} for i,name in enumerate(names)]
            code,run,called=self.run_case(query,authors)
            self.assertEqual(code,0)
            self.assertEqual(run["author_mode"],"ambiguous")
            self.assertEqual(len(run["author_candidates"]),2)
            self.assertEqual(called,[])

    def test_unique_id_and_no_match_preserves_seed_authors(self):
        authors=[{"name":"Wei Li","identifiers":ids(semantic_scholar="101")},
                 {"name":"Ming Li","identifiers":ids(semantic_scholar="202")}]
        code,run,called=self.run_case("Wei Li",authors)
        self.assertEqual(called,["101"])
        self.assertEqual(run["author_mode"],"papers")
        code,run,called=self.run_case("Nobody",authors)
        self.assertEqual(code,0)
        self.assertEqual(run["source_status"]["semantic-scholar"]["status"],"not_found")
        self.assertEqual(len(run["author_candidates"]),2)
        self.assertEqual(called,[])

    def test_failed_seed_does_not_assert_author_absence(self):
        for query in (None,"Li"):
            code,run,called=self.run_case(query,failed=True)
            self.assertEqual(code,3)
            self.assertEqual(run["status"],"failed")
            self.assertEqual(run["author_mode"],"unresolved")
            self.assertEqual(run["source_status"]["semantic-scholar"]["status"],"error")
            self.assertEqual(called,[])


if __name__=="__main__": unittest.main()
