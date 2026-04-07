"""LDAP authentication client.

Supports two authentication patterns:
1. Direct bind: construct user DN from template, bind directly
2. Search-then-bind: search for user with service account, then bind as user

Group membership checks are optional and configurable.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ldap3 is optional — only required when AUTH_ENABLED=true
try:
    from ldap3 import ALL, SUBTREE, Connection, Server
    from ldap3.core.exceptions import LDAPException

    LDAP3_AVAILABLE = True
except ImportError:
    LDAP3_AVAILABLE = False


class LDAPAuthenticator:
    """Authenticate users against an LDAP directory."""

    def __init__(
        self,
        server_url: str | None = None,
        base_dn: str | None = None,
        user_dn_template: str | None = None,
        bind_dn: str | None = None,
        bind_password: str | None = None,
        group_dn: str | None = None,
        use_tls: bool | None = None,
        search_filter: str | None = None,
        auth_method: str | None = None,
    ):
        self.server_url = server_url or os.getenv(
            "LDAP_SERVER", "ldap://localhost:389"
        )
        self.base_dn = base_dn or os.getenv("LDAP_BASE_DN", "dc=example,dc=com")
        self.user_dn_template = user_dn_template or os.getenv(
            "LDAP_USER_DN_TEMPLATE",
            "uid={username},ou=users,dc=example,dc=com",
        )
        self.bind_dn = bind_dn or os.getenv("LDAP_BIND_DN", "")
        self.bind_password = bind_password or os.getenv("LDAP_BIND_PASSWORD", "")
        self.group_dn = group_dn or os.getenv("LDAP_GROUP_DN", "")
        self.search_filter = search_filter or os.getenv(
            "LDAP_SEARCH_FILTER", "(uid={username})"
        )
        self.auth_method = auth_method or os.getenv("LDAP_AUTH_METHOD", "direct")

        if use_tls is not None:
            self.use_tls = use_tls
        else:
            self.use_tls = os.getenv("LDAP_USE_TLS", "false").lower() == "true"

    def authenticate(self, username: str, password: str) -> tuple[bool, str]:
        """Authenticate a user. Returns (success, message)."""
        if not LDAP3_AVAILABLE:
            logger.error("ldap3 library not installed — cannot authenticate")
            return False, "LDAP library not available"

        if not username or not password:
            return False, "Username and password required"

        if self.auth_method == "search":
            return self._search_and_bind(username, password)
        return self._direct_bind(username, password)

    def _direct_bind(self, username: str, password: str) -> tuple[bool, str]:
        """Direct bind: construct DN from template and bind."""
        try:
            server = Server(self.server_url, get_info=ALL, use_ssl=self.use_tls)
            user_dn = self.user_dn_template.format(username=username)

            conn = Connection(server, user=user_dn, password=password, auto_bind=True)

            if self.group_dn:
                if not self._check_group(conn, user_dn):
                    conn.unbind()
                    return False, "User not in authorized group"

            conn.unbind()
            logger.info(f"LDAP direct-bind auth success: {username}")
            return True, "Authenticated"

        except LDAPException as e:
            logger.warning(f"LDAP direct-bind failed for {username}: {e}")
            return False, "Invalid credentials"
        except Exception as e:
            logger.error(f"LDAP connection error: {e}")
            return False, "Authentication service unavailable"

    def _search_and_bind(self, username: str, password: str) -> tuple[bool, str]:
        """Search for user with service account, then bind as user."""
        try:
            server = Server(self.server_url, get_info=ALL, use_ssl=self.use_tls)

            # Bind with service account
            if self.bind_dn and self.bind_password:
                svc_conn = Connection(
                    server,
                    user=self.bind_dn,
                    password=self.bind_password,
                    auto_bind=True,
                )
            else:
                svc_conn = Connection(server, auto_bind=True)

            # Search for user
            search_filter = self.search_filter.format(username=username)
            svc_conn.search(
                self.base_dn,
                search_filter,
                search_scope=SUBTREE,
                attributes=["dn"],
            )

            if not svc_conn.entries:
                svc_conn.unbind()
                # Generic message — don't reveal user existence
                return False, "Invalid credentials"

            user_dn = str(svc_conn.entries[0].entry_dn)
            svc_conn.unbind()

            # Bind as the discovered user
            user_conn = Connection(
                server, user=user_dn, password=password, auto_bind=True
            )

            if self.group_dn:
                if not self._check_group(user_conn, user_dn):
                    user_conn.unbind()
                    return False, "User not in authorized group"

            user_conn.unbind()
            logger.info(f"LDAP search-bind auth success: {username}")
            return True, "Authenticated"

        except LDAPException as e:
            logger.warning(f"LDAP search-bind failed for {username}: {e}")
            return False, "Invalid credentials"
        except Exception as e:
            logger.error(f"LDAP connection error: {e}")
            return False, "Authentication service unavailable"

    def _check_group(self, conn: Connection, user_dn: str) -> bool:
        """Check if user is a member of the required group."""
        try:
            # Try groupOfNames (member attribute)
            search_filter = f"(&(objectClass=groupOfNames)(member={user_dn}))"
            conn.search(self.group_dn, search_filter, search_scope=SUBTREE)
            if conn.entries:
                return True

            # Try groupOfUniqueNames (uniqueMember attribute)
            search_filter = f"(&(objectClass=groupOfUniqueNames)(uniqueMember={user_dn}))"
            conn.search(self.group_dn, search_filter, search_scope=SUBTREE)
            if conn.entries:
                return True

            # Try posixGroup (memberUid attribute — uses username, not DN)
            uid = user_dn.split(",")[0].split("=")[1] if "=" in user_dn else ""
            if uid:
                search_filter = f"(&(objectClass=posixGroup)(memberUid={uid}))"
                conn.search(self.group_dn, search_filter, search_scope=SUBTREE)
                if conn.entries:
                    return True

            return False
        except Exception as e:
            logger.warning(f"Group membership check failed: {e}")
            return False
