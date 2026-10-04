import os
import pwd
import unittest
from unittest.mock import patch

from docwork.access import AccessDenied, LocalAccess, Principal


class AccessTests(unittest.TestCase):
    def test_identity_comes_from_os_account_and_capabilities_expire_on_restart(self):
        with patch.dict(os.environ, {"USER": "extractor", "LOGNAME": "forged"}):
            first, second = LocalAccess(), LocalAccess()
        self.assertEqual(first.reviewer.actor, f"local:{pwd.getpwuid(os.getuid()).pw_name}")
        self.assertIsNone(second.authenticate(first.reviewer_token, None))
        self.assertIsNone(second.authenticate(None, f"Bearer {first.processing_token}"))
        self.assertNotEqual(first.reviewer_token, first.processing_token)
        self.assertIsNone(first.authenticate(first.processing_token, None))
        self.assertIsNone(first.authenticate(None, f"Bearer {first.reviewer_token}"))
        self.assertEqual(first.authenticate(first.reviewer_token, f"Bearer {first.processing_token}"), first.processor)

    def test_unknown_roles_and_client_actor_cannot_gain_review(self):
        access = LocalAccess()
        for principal in (access.processor, Principal("extractor", "unknown")):
            with self.assertRaises(AccessDenied):
                access.require_route(principal, "POST", "/api/documents/approve")
            with self.assertRaises(AccessDenied):
                access.review_actor(principal, {"actor": access.reviewer.actor})
        self.assertEqual(access.review_actor(access.reviewer, {}), access.reviewer.actor)


if __name__ == "__main__":
    unittest.main()
