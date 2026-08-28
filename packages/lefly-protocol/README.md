# lefly-protocol

Standard-library Python types and validators for LeFly Device Protocol v1.

The package provides:

- immutable command and event envelopes;
- the canonical nine-command and nine-event catalogs;
- strict validation for known payloads, complete state snapshots,
  capabilities, lifecycle correlation, and structured errors;
- additive handling for syntactically valid unknown v1 message types.

JSON Schema is the normative wire-structure contract. The Python validator
adds semantic checks that JSON Schema cannot fully express, including finite
numbers, joint limits, queue capacity, matrix frame size, and state/device
identity consistency.

```python
from lefly_protocol import DeviceCommand, ProtocolError

try:
    command = DeviceCommand.from_dict(message)
except ProtocolError as exc:
    print(exc)
```

Known payloads are closed objects. Experimental fields belong under
namespaced `extensions`; hardware-driver data structures do not cross this
package boundary. See [`docs/protocol.md`](../../docs/protocol.md) for the
catalog and behavior, and [`contracts/`](../../contracts/) for schemas,
canonical examples, and conformance fixtures.
