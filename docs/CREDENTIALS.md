# Local API credentials

The private credential file is `resources/private/openai_api_key`. It is ignored
by Git. Keep its permissions owner-only (`chmod 600 resources/private/openai_api_key`).
Do not paste the key into chat, a command, or a tracked file.

The file accepts either the key alone or a dotenv-style assignment:

```dotenv
OPENAI_API_KEY=YOUR_KEY_HERE
```

Matching single or double quotes and an optional `export` prefix are supported.
`AI_API_KEY` is also accepted for an existing app configuration. Use exactly one
key assignment: repeating a name or including both names is rejected.
Blank lines, comment lines, and other `KEY=VALUE` settings are ignored. This is
literal text parsing: variables and shell commands are never expanded or run.
The file must be UTF-8, no larger than 64 KiB, and contain one nonempty key
without spaces or other whitespace.

The runner uses the file only when explicitly given both `--live` and
`--api-key-file resources/private/openai_api_key`. Without `--api-key-file`, live
mode reads `OPENAI_API_KEY` from the existing process environment. It does not
automatically load `.env`. Planning and replay need no credentials.

Application settings such as `PROVIDER`, `AI_MODEL`, `AI_BASE_URL`, and
`AI_REASONING_EFFORT` in this file do not configure this repository's runner.
Select the model with `--model` and, when supported by that model, reasoning with
`--reasoning-effort`. Use the same settings for SQL and SQLP. The live adapter
uses OpenAI's Responses endpoint; a compatible-provider URL is not configurable.

See [the pilot guide](MODEL_PILOT.md) for the complete run command, request cap,
and fresh journal requirement. Credentials and their file path are excluded
from model requests and saved experiment manifests. Request and output-token
limits are separate from a dollar budget.
