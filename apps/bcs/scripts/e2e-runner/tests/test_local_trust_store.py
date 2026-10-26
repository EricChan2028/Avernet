"""Independent local storage absence survives API filtering and swallowed errors."""
import contextlib
import json
import sqlite3
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.evidence import EvidenceError
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.local_sqlite import OwnedSqliteDatabase
from bcs_e2e.local_trust_store import observe_legacy_baseline
from bcs_e2e.trust_baseline import ROLES, TrustBaselineError
import test_local_fixture_sets as fixtures
import test_trust_baseline as baseline


class LocalTrustStoreTests(unittest.TestCase):
    response = fixtures.LocalFixtureSetTests.response

    def setUp(self):
        fixtures.LocalFixtureSetTests.setUp(self)
        self.bound = OwnedLocalFixtures(self.runtime,fixture_set='trusted-team')
        self.path = self.runtime.root/'bcs.sqlite'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE bcs_friendships(env TEXT,left_bot TEXT,right_bot TEXT);
            CREATE TABLE bcs_friend_requests(env TEXT,from_bot TEXT,to_bot TEXT,status TEXT);''')
        meta=self.path.stat(); self.runtime.database_identity=(meta.st_dev,meta.st_ino)
        with patch.object(self.bound,'_request',side_effect=self.response):
            self.bound.apply(allow_write=True)
        self.bindings={role:'bot-'+role for role in ROLES}

    def observe(self):
        with patch.object(self.bound,'_request',side_effect=self.response):
            return observe_legacy_baseline(self.bound,self.bindings)

    def test_exact_scope_ignores_foreign_env_and_unrelated_actors_without_mutation(self):
        self.db.executescript('''INSERT INTO bcs_friendships VALUES ('prod','bot-CEO','bot-PM');
            INSERT INTO bcs_friendships VALUES ('local','foreign-A','foreign-B');
            INSERT INTO bcs_friend_requests VALUES ('prod','bot-CEO','bot-PM','pending');
            INSERT INTO bcs_friend_requests VALUES ('local','foreign-A','foreign-B','pending');''')
        before=self.path.read_bytes(); receipt=self.bound.path.read_bytes()
        result=self.observe()
        self.assertEqual(result['status'],'OBSERVED')
        self.assertEqual(len(result['rows']),5)
        self.assertEqual(result['business_queries'],1)
        self.assertEqual(result['storage_writes'],0)
        self.assertEqual(result['edge_permission_graph'],'NOT_CHECKED')
        self.assertTrue(all(row['friendship_present']==row['sent_request_present']==row['received_request_present']==0
                            for row in result['rows']))
        self.assertEqual(self.path.read_bytes(),before);self.assertEqual(self.bound.path.read_bytes(),receipt)

    def test_missing_deleted_or_external_peer_rows_cannot_be_hidden_by_api_enrichment(self):
        for left,right in (('bot-CEO','deleted-peer'),('missing-peer','bot-CS'),('bot-PM','human-owner')):
            self.db.execute('INSERT INTO bcs_friendships VALUES (?,?,?)',('local',left,right));self.db.commit()
            with self.assertRaises(TrustBaselineError): self.observe()
            self.db.execute('DELETE FROM bcs_friendships');self.db.commit()  # Synthetic test DB only.

    def test_pending_and_malformed_or_terminal_history_block_fresh_fixture_baseline(self):
        for direction in ('sent','received'):
            for status in ('pending','accepted','rejected','malformed',None):
                with self.subTest(direction=direction,status=status):
                    sender,receiver=('bot-QA','unknown-peer') if direction=='sent' else ('unknown-peer','bot-PM')
                    self.db.execute('INSERT INTO bcs_friend_requests VALUES (?,?,?,?)',('local',sender,receiver,status));self.db.commit()
                    with self.assertRaises(TrustBaselineError): self.observe()
                    self.db.execute('DELETE FROM bcs_friend_requests');self.db.commit()

    def test_missing_table_view_or_missing_column_is_not_zero_evidence(self):
        self.db.execute('DROP TABLE bcs_friend_requests');self.db.commit()
        with self.assertRaises(TrustBaselineError): self.observe()
        self.db.execute('CREATE VIEW bcs_friend_requests AS SELECT NULL AS env,NULL AS from_bot,NULL AS to_bot,NULL AS status');self.db.commit()
        with self.assertRaises(TrustBaselineError): self.observe()
        self.db.execute('DROP VIEW bcs_friend_requests')
        self.db.execute('CREATE TABLE bcs_friend_requests(env TEXT, from_bot TEXT, to_bot TEXT)');self.db.commit()
        with self.assertRaises(TrustBaselineError): self.observe()

    def test_query_failure_and_replaced_database_never_return_success(self):
        original=OwnedSqliteDatabase._connection
        @contextlib.contextmanager
        def deny_query(database):
            with original(database) as connection:
                connection.set_authorizer(lambda action,*_args: sqlite3.SQLITE_DENY
                    if action==sqlite3.SQLITE_READ and _args[0]=='bcs_friend_requests' else sqlite3.SQLITE_OK)
                yield connection
        with patch.object(OwnedSqliteDatabase,'_connection',deny_query),self.assertRaises(EvidenceError):self.observe()
        self.path.rename(self.path.with_suffix('.old'))
        with sqlite3.connect(self.path) as replacement:replacement.execute('CREATE TABLE marker(id INTEGER)')
        with self.assertRaises(EvidenceError):self.observe()

    def test_fixed_role_ids_cannot_be_replaced_by_other_fixture_or_sql(self):
        for value in ('foreign',"x' OR 1=1"):
            changed={**self.bindings,'CEO':value}
            with patch.object(self.bound,'_request',side_effect=self.response), \
                 patch.object(OwnedSqliteDatabase,'_connection') as connection,self.assertRaises(TrustBaselineError):
                observe_legacy_baseline(self.bound,changed)
            connection.assert_not_called()

    def test_api_empty_with_stored_conflict_is_blocked_and_holder_closed(self):
        # Rebuild a fresh receipt in this isolated test runtime for managed capture.
        self.bound.path.unlink()
        with OwnedLocalRoleIdentity(self.bound,roles=ROLES) as identity:
            with patch.object(self.bound,'_request',side_effect=self.response):
                self.bound.apply(allow_write=True,role_identity=identity)
            with patch.object(identity,'_request',side_effect=baseline.TrustBaselineTests.read_response):
                for role in ROLES:identity.verify(role)
                self.db.execute('INSERT INTO bcs_friendships VALUES (?,?,?)',('local','bot-PM','deleted-peer'));self.db.commit()
                with patch.object(self.bound,'_request',side_effect=self.response),self.assertRaises(TrustBaselineError):
                    identity.trust_baseline()
            self.assertEqual(identity.summary()['credential_memory'],'CLEARED')
