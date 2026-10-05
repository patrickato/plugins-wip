#!/usr/bin/env python3
"""
badhid_setopt.py - safely set ONE option inside the [main.plugins.badhid_ng]
block of config.toml, without touching any other plugin. Used by the setup
wizard so a newcomer never has to hand-edit TOML.

  sudo python3 badhid_setopt.py <config.toml> <key> <value>

Value is written verbatim, so quote strings yourself:
  ... enabled true
  ... bind_scope '"lan"'
Exits non-zero (and changes nothing) if the badhid block is missing or if the
result wouldn't parse.
"""
import re
import sys


def set_in_block(text, key, value):
    # Match the REAL section header (start of a line), not a mention of it in a
    # comment, and run to the next top-level [section] header or EOF.
    pat = re.compile(r'(?ms)^\[main\.plugins\.badhid_ng\].*?(?=^\[|\Z)')
    m = pat.search(text)
    if not m:
        return None, "no [main.plugins.badhid_ng] block found"
    block = m.group(0)
    kpat = re.compile(r'(?m)^(\s*%s\s*=\s*).*$' % re.escape(key))
    if kpat.search(block):
        newblock = kpat.sub(lambda mm: mm.group(1) + value, block, count=1)
    else:
        newblock = block.rstrip("\n") + "\n%s = %s\n" % (key, value)
    return text[:m.start()] + newblock + text[m.end():], None


def main():
    if len(sys.argv) != 4:
        print(__doc__); sys.exit(2)
    path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    new, err = set_in_block(text, key, value)
    if err:
        print("error:", err); sys.exit(1)
    # validate it still parses (tomllib on 3.11+, else tomlkit, else skip)
    try:
        import tomllib
        tomllib.loads(new)
    except ModuleNotFoundError:
        try:
            import tomlkit
            tomlkit.parse(new)
        except Exception as exc:
            print("refusing: result won't parse:", exc); sys.exit(1)
    except Exception as exc:
        print("refusing: result won't parse:", exc); sys.exit(1)
    import os
    tmp = path + ".badhid.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(new)
    os.replace(tmp, path)
    print(f"set {key} = {value} in [main.plugins.badhid_ng]")


if __name__ == "__main__":
    main()
