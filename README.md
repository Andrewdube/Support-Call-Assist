# Call Assist — AI co-pilot for Lenovo hardware/software support calls

Listens to a support call, transcribes both sides live, and gives the agent:

- **Troubleshooting steps** ordered least → most invasive (power drain, Lenovo Diagnostics, BIOS/drivers via System Update, BIOS rollback, then hardware), each tagged as coming from the **KB** or **general** knowledge.
- **Parts needed** with likelihood (confirmed / likely / possible). FRU numbers are only shown when they appear in a KB article — the AI is told never to invent them.
- **Questions to ask next**, and an **escalation flag** (e.g. the ThinkStation BIOS-defect pattern).
- **Automatic Dynamics 365 case**: once the caller's name, phone/email and the reason for the call are heard, it finds or creates the Contact, opens a Case (origin = Phone), keeps the description updated, and on hang-up attaches the troubleshooting summary and full transcript as Notes.

```
Call audio ─► Azure Speech (speaker-separated transcript)
                    │
                    ▼
            Knowledge base search (local articles + Dynamics knowledge articles)
                    │
                    ▼
            Claude ─► steps · parts · questions · customer details · reason for call
                    │                         │
                    ▼                         ▼
            Agent dashboard (browser)    Dynamics 365: Contact + Case + Notes
```

## Quick start (demo — no audio or Dynamics needed)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # add at least ANTHROPIC_API_KEY
python run.py
```

Open http://127.0.0.1:8000, keep **Demo call** selected and press **Start call**. It replays `samples/demo_call.txt` (a T14 Gen 3 no-power call). Without Dynamics settings, the ticket panel shows a **dry run** of what would be created.

## Going live

**1. Audio (Azure AI Speech)** — create a Speech resource in Azure, put the key/region in `.env`, choose **Live call**.
- Headset calls: the default mic only hears you. To capture both sides from a softphone (Teams, Five9, Genesys, etc.), route audio through a loopback device (e.g. VB-Audio Virtual Cable / VoiceMeeter on Windows) and set `AUDIO_INPUT_DEVICE`.
- Contact-centre platforms can also stream call audio directly (Dynamics 365 Contact Center / Teams real-time media, Genesys AudioHook, Amazon Connect streaming); swap `AzureCallTranscriber` for a stream reader if you go that route.

**2. Dynamics 365**
1. Entra ID → App registrations → New registration; create a client secret.
2. Power Platform admin center → your environment → Settings → Users → **Application users** → add the app, give it a role that can read/write Contacts, Cases and Notes and read Knowledge Articles.
3. Fill in `DYNAMICS_URL`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`.

If your org stores serial number / machine type / product in custom Case fields, add them in `DynamicsClient.create_case` (they currently go in a Note).

**3. Knowledge base**
- Published Dynamics knowledge articles are loaded at startup (`SYNC_DYNAMICS_KB=true`).
- Drop extra `.md`/`.txt` articles into `kb/`. Front matter (`models:`, `symptoms:`, `parts:` with real FRUs) boosts search accuracy and lets the AI quote part numbers. The four included articles are **samples** — replace them with your approved content.

## Files

| Path | Purpose |
|---|---|
| `app/server.py` | Web server, call session, ticket sync logic |
| `app/assistant.py` | Prompt + structured output schema for Claude |
| `app/kb.py` | Article loader and BM25 search |
| `app/dynamics.py` | Dataverse Web API: contacts, cases, notes, knowledge articles |
| `app/transcriber.py` | Azure live transcription + scripted demo mode |
| `app/static/index.html` | Agent dashboard |

## Before using on real calls

- **Consent:** many US states (e.g. CA, FL, IL, PA, WA) require all parties to consent to recording/transcription. Add a disclosure to your IVR or greeting, and check company policy.
- **Data handling:** transcripts contain customer PII and are sent to Azure Speech and the Anthropic API. Get security/privacy sign-off and confirm the data-retention settings meet your org's requirements.
- The agent remains responsible for verifying entitlement and approving every part dispatch; suggestions are advisory.
