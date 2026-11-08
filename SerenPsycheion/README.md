# seren-psycheion

The console and face workshop for a [SerenSoma](https://github.com/ChadRoesler/SerenSoma) body.

```
pip install seren-psycheion
seren-psycheion              # http://127.0.0.1:7429/viewer
seren-psycheion --describe   # one line of JSON, for installers
```

## Configure

Copy [`seren-psycheion.yaml.sample`](seren-psycheion.yaml.sample) to
`~/seren-psycheion/seren-psycheion.yaml` (or point `--config` or
`$SEREN_PSYCHEION_CONFIG` at it):

```yaml
soma:
  robot:
    url: http://192.0.2.21:8080
    token_file: ~/seren-soma/state/control_token
  voice:
    url: http://127.0.0.1:7480
    token_file: ~/seren-soma/state/token
```

The body's tokens are pointers: `token_file`, `token_env` (an environment
variable's name) or `token_keyring` (`service/username` in the OS keychain).
`token:` inline is the escape hatch. Psycheion makes every call to the body
itself; the page never sees these.

## Exposure

Psycheion binds to loopback. It can change a robot's lights and read what a
hand did, so a wider bind with no `server.bearer_token_env` refuses to start
(Meninges' `enforce_server`), and says how to fix it.

## Routes

| | |
|---|---|
| `GET /viewer` | The console. |
| `GET /api/soma` | The robot's status and the voice server's status and settings, each with why it didn't answer if it didn't. |
| `POST /api/soma/robot/settings` | `dimmer`, `volume` (0 to 100), `brightness` (5 to 100); saved on the robot. |
| `POST /api/soma/voice/settings` | Any part of the voice server's settings (night, work hours, overwhelm). |
| `GET /api/soma/touch?after=N` | The touch feed since event N, and overwhelm's progress. |

Everything under `/api` needs the bearer when one is set.
