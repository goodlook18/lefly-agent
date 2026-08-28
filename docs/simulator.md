# Simulator Lite And Web Console

English | [简体中文](zh-CN/simulator.md)

Simulator Lite is a hardware-free LeFly Device Protocol endpoint and the local server for the unified browser Console.

## Run the packaged Console

Install and start from the repository root:

```bash
python -m pip install \
  packages/lefly-protocol \
  packages/lefly-sdk-python \
  packages/lefly-simulator
python -m lefly_simulator --host 127.0.0.1 --port 8766
```

Open `http://127.0.0.1:8766/`. The Console supports target state, five-joint
motion, preset actions, head-light controls, robot status rendering, virtual
sensor injection, diagnostics, and Agent text when the separate Agent runs.

## Optional remote target  for the Console

```bash
python -m lefly_simulator \
  --remote ws://robot-host:8766/ws/device \
  --host 127.0.0.1 \
  --port 8766
```

> This alternative command adds a remote Device Protocol endpoint to the Console. It does not start the Text Agent or change the Agent's configured `device_url`.

Only one target is selected at a time; commands are never broadcast. Remote disconnect does not silently fall back to the Simulator. Virtual sensor injection is available only for the built-in Simulator target.

## Rebuild the Console

Contributor builds require Node.js 22.12 or newer:

```bash
cd packages/lefly-console-web
npm ci
npm test
npm run build
```

Vite writes the production build into
`packages/lefly-simulator/src/lefly_simulator/static/`. Commit the generated
fingerprinted assets with the source change that produced them.

## Control model

- One browser session owns the renewable control lease. A read-only page can
  explicitly take over, which immediately makes the previous page read-only.
- State and advertised capabilities gate every control.
- Revision gaps disable mutation until complete state is restored.
- The head matrix is user-controlled.
- The base status strip is robot-owned and read-only.
- Gesture and face IDs are raw values; the Console does not assign permanent meanings.

## Troubleshooting

- **Frontend unavailable:** rebuild the Console and restart Simulator.
- **Read-only controls:** acquire the control lease or close the session that owns it.
- **Stale/offline target:** restore the endpoint and wait for a complete snapshot.
- **Blank model:** enable WebGL and hardware acceleration.
- **Remote target unavailable:** check its URL and Device Protocol v1 behavior.
