# VBScript oracle

Compares the cross-platform VBScript interpreter
(`packages/prod/src/xknxeditor/prod/script/vbscript/`) with the Windows VBScript engine in
32-bit mode (`SysWOW64\cscript.exe`), the engine VBScript parameter calculations run in.
Windows only; nothing here is needed at run time.

Record the engine's results for the conformance probes
(`packages/prod/tests/script/vbscript/corpus.py`); they are committed as
`packages/prod/tests/script/vbscript/golden/vbscript.json`, and the tests compare the interpreter
with them on every OS:

```
uv run python tools/vbscript_oracle/record.py
uv run python tools/vbscript_oracle/record.py --only stmt:
```

Compare with the calculations of real products. `extract.py` collects every distinct VBScript
calculation of a folder of `.knxprod` files; `archive.py` runs each LR and RL transformation with
the parameter defaults, minimum, maximum, zero and random values in the engine and in the
interpreter and writes every difference to the report. The extracted file and the report contain
manufacturer code: keep them outside the repository.

```
uv run python tools/vbscript_oracle/extract.py C:/knxprod-archive %TEMP%/vbs/calculations.jsonl
uv run python tools/vbscript_oracle/archive.py %TEMP%/vbs/calculations.jsonl --report %TEMP%/vbs/diff.txt
uv run python tools/vbscript_oracle/archive.py %TEMP%/vbs/calculations.jsonl --locale de-DE --seed 2
```
