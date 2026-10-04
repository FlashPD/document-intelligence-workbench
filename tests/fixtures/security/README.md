# Fictional adversarial invoice PDFs

These five small documents are explicitly self-authored security fixtures. They contain no real invoice, private information, scanner capture or human-study result. Their complete bytes are bound in [manifest.json](manifest.json).

- `plain-control.pdf` contains the fictional Aster invoice with one row and a printed total of `270.00`.
- `encrypted-empty-password.pdf` and `encrypted-password.pdf` encrypt that same invoice with legacy RC4-128, using empty and `fixture-open-only` user passwords. The fictional owner password is `fixture-owner-only`. These exercise refusal of encrypted PDFs, not the security of this legacy algorithm.
- `document-instructions.pdf` and `authority-json.pdf` add document notes demanding approval/export, altered values, credentials, tools and a privileged actor. They are untrusted invoice text; no instruction is executed by fixture preparation or intended as a user command.

Generation uses [generate_security_fixtures.py](../../../scripts/generate_security_fixtures.py) and [pypdf 6.1.1](https://pypi.org/project/pypdf/6.1.1/) in separate, explicitly installed tooling. [pypdf's encryption documentation](https://pypdf.readthedocs.io/en/6.1.1/user/encryption-decryption.html) describes the algorithm options. Neither production parsing nor deterministic tests import pypdf. Runtime pdfinfo independently confirms valid encryption/page count using the fictional password before each production refusal. Regenerating into a new directory may change encrypted bytes; do not overwrite a frozen fixture or its historical evidence.

```sh
python3.12 -m pip install --no-deps --target artifacts/security-fixture-tooling pypdf==6.1.1
PYTHONPATH=artifacts/security-fixture-tooling python3.12 scripts/generate_security_fixtures.py \
  --output-dir artifacts/security-fixtures-regenerated
```
