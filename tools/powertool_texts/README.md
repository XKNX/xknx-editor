# PowerTool Interface Texts

The vendor package's `powertool/text-map.json` maps technical node identifiers to
independently written English labels and ordered choices. Encoding metadata is
separate. Keep option order and units unchanged when editing the texts.
The updater formats UTF-8 JSON with sorted keys and one node per line.
`default_text` keeps the original English device text as the translation key for
the suggested default. Stored text values and image defaults remain unchanged
when the product language changes.

Translations use the `powertool` gettext domain in the same package. After changing
English texts, update the catalogs, translate the new entries in every language,
and compile:

```sh
uv run --with babel python tools/powertool_texts/update.py
uv run --with babel pybabel compile -d packages/vendor/src/xknxeditor/vendor/powertool/locales -D powertool
uv run pytest apps/editor-gui/tests/test_translations.py -k powertool
```

The updater does not guess translations for changed messages. All editor languages
are checked, including placeholders, timer units and distinguishable options.
