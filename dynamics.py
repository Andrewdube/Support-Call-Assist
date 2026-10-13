"""Microsoft Dynamics 365 Customer Service (Dataverse Web API) client.

Auth uses an Azure AD app registration (client credentials) that has been added
as an Application User in the Dynamics environment with a security role that
can read/write Contacts, Cases (incident), Notes (annotation) and read
Knowledge Articles.
"""
from __future__ import annotations

import html
import logging
import re
import time

import httpx
import msal

from .config import settings
from .kb import Article

log = logging.getLogger(__name__)

API = "/api/data/v9.2"
CASE_ORIGIN_PHONE = 1  # caseorigincode option value for "Phone"
KB_STATE_PUBLISHED = 3


def _strip_html(text: str) -> str:
    text = re.sub(r"<(script|style).*?</\1>", " ", text or "", flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", html.unescape(text)).strip()


def _odata_str(value: str) -> str:
    return value.replace("'", "''")


class DynamicsClient:
    def __init__(self) -> None:
        if not settings.dynamics_configured:
            raise RuntimeError("Dynamics settings missing (DYNAMICS_URL, AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET)")
        self._app = msal.ConfidentialClientApplication(
            settings.azure_client_id,
            authority=f"https://login.microsoftonline.com/{settings.azure_tenant_id}",
            client_credential=settings.azure_client_secret,
        )
        self._token: str | None = None
        self._token_exp = 0.0
        self._http = httpx.Client(base_url=settings.dynamics_url + API, timeout=30)

    # ---------- plumbing ----------
    def _headers(self) -> dict:
        if not self._token or time.time() > self._token_exp - 120:
            res = self._app.acquire_token_for_client(scopes=[f"{settings.dynamics_url}/.default"])
            if "access_token" not in res:
                raise RuntimeError(f"Dynamics auth failed: {res.get('error_description', res)}")
            self._token, self._token_exp = res["access_token"], time.time() + res.get("expires_in", 3600)
        return {
            "Authorization": f"Bearer {self._token}",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "Prefer": "return=representation",
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = self._http.get(path, params=params, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = self._http.post(path, json=body, headers=self._headers())
        r.raise_for_status()
        return r.json() if r.content else {}

    def _patch(self, path: str, body: dict) -> None:
        r = self._http.patch(path, json=body, headers=self._headers())
        r.raise_for_status()

    # ---------- contacts ----------
    def find_contact(self, email: str | None = None, phone: str | None = None) -> dict | None:
        filters = []
        if email:
            filters.append(f"emailaddress1 eq '{_odata_str(email)}'")
        if phone:
            digits = re.sub(r"\D", "", phone)[-10:]
            if len(digits) >= 7:
                filters.append(f"contains(telephone1,'{digits[-4:]}')")
        if not filters:
            return None
        data = self._get("/contacts", {
            "$select": "contactid,fullname,firstname,lastname,emailaddress1,telephone1",
            "$filter": " or ".join(filters),
            "$top": "10",
        })
        rows = data.get("value", [])
        # Prefer exact email match, then exact phone digits match.
        for row in rows:
            if email and (row.get("emailaddress1") or "").lower() == email.lower():
                return row
        if phone:
            want = re.sub(r"\D", "", phone)[-10:]
            for row in rows:
                if re.sub(r"\D", "", row.get("telephone1") or "")[-10:] == want:
                    return row
        return None

    def upsert_contact(self, details: dict) -> str:
        """Find the caller by email/phone, filling in blanks; else create. Returns contactid."""
        existing = self.find_contact(details.get("email"), details.get("phone"))
        fields = {
            "firstname": details.get("first_name"),
            "lastname": details.get("last_name"),
            "emailaddress1": details.get("email"),
            "telephone1": details.get("phone"),
        }
        fields = {k: v for k, v in fields.items() if v}
        if existing:
            missing = {k: v for k, v in fields.items() if not existing.get(k)}
            if missing:
                self._patch(f"/contacts({existing['contactid']})", missing)
            return existing["contactid"]
        if "lastname" not in fields:
            fields["lastname"] = details.get("full_name") or "Unknown caller"
        return self._post("/contacts", fields)["contactid"]

    # ---------- cases ----------
    def create_case(self, contact_id: str, title: str, description: str, serial: str | None = None) -> dict:
        body = {
            "title": title[:200],
            "description": description,
            "caseorigincode": CASE_ORIGIN_PHONE,
            "customerid_contact@odata.bind": f"/contacts({contact_id})",
        }
        case = self._post("/incidents", body)
        if serial:
            # Serial numbers vary by org; store as a note unless you add a custom field.
            self.add_note(case["incidentid"], "Device serial / machine type", serial)
        return {"incidentid": case["incidentid"], "ticketnumber": case.get("ticketnumber")}

    def update_case(self, case_id: str, description: str) -> None:
        self._patch(f"/incidents({case_id})", {"description": description})

    def add_note(self, case_id: str, subject: str, text: str) -> None:
        self._post("/annotations", {
            "subject": subject[:500],
            "notetext": text,
            "objectid_incident@odata.bind": f"/incidents({case_id})",
        })

    # ---------- knowledge base ----------
    def fetch_knowledge_articles(self, limit: int = 2000) -> list[Article]:
        out: list[Article] = []
        params = {
            "$select": "knowledgearticleid,title,content,keywords,articlepublicnumber,description",
            "$filter": f"statecode eq {KB_STATE_PUBLISHED} and islatestversion eq true",
        }
        path: str | None = "/knowledgearticles"
        headers_extra = {"Prefer": "odata.maxpagesize=200"}
        while path and len(out) < limit:
            r = self._http.get(path, params=params, headers={**self._headers(), **headers_extra})
            r.raise_for_status()
            data = r.json()
            for row in data.get("value", []):
                out.append(Article(
                    id=f"dynamics:{row.get('articlepublicnumber') or row['knowledgearticleid']}",
                    title=row.get("title") or "Untitled",
                    body=_strip_html(row.get("content") or row.get("description") or ""),
                    source="dynamics",
                    meta={"keywords": row.get("keywords") or ""},
                ))
            path, params = data.get("@odata.nextLink"), None
            if path:
                path = path.replace(settings.dynamics_url + API, "")
        return out
