
## Safe domains for WebFetch tool

```json
{
  "permissions": {
    "allow": [
      "Skill(p:requirements)",
      "Skill(p:skill-writer)",
      "Skill(p:implementation-plan)",
      "Skill(p:c)",

      "Bash(~/.claude/skills/p:requirements/update_tasks.py:*)",
      "Bash(~/.claude/skills/p:requirements/show_tasks.py:*)",
      "Bash(~/.claude/skills/p:requirements/show_task_details.py:*)",

      "Bash(pkg-config:*)",
      "Bash(grep:*)",
      "Bash(make:*)",
      "Bash(cmake:*)",
      "Bash(find:*)",
      "Bash(ls:*)",
      "Bash(ctest:*)",
      "Bash(ldd:*)",
      "Bash(nm:*)",
      "Bash(xxd:*)",
      "Bash(file:*)",

      "WebFetch(domain:cheatsheetseries.owasp.org)",
      "WebFetch(domain:www.portainer.io)",
      "WebFetch(domain:betterstack.com)",
      "WebFetch(domain:www.practical-devsecops.com)",
      "WebFetch(domain:www.sysdig.com)",
      "WebFetch(domain:accuknox.com)",
      "WebFetch(domain:docs.docker.com)",
      "WebFetch(domain:www.wiz.io)",
      "WebFetch(domain:www.anthropic.com)",
      "WebFetch(domain:jannesklaas.github.io)",
      "WebFetch(domain:medium.com)",
      "WebFetch(domain:mikhail.io)",
      "WebFetch(domain:leehanchung.github.io)",
      "WebFetch(domain:www.pubnub.com)",
      "WebFetch(domain:docs.anthropic.com)",
      "WebFetch(domain:github.com)",
      "WebFetch(domain:grep.app)",
      "WebFetch(domain:raw.githubusercontent.com)",
      "WebFetch(domain:docs.pytorch.org)",
      "WebFetch(domain:sebastianraschka.com)",
      "WebFetch(domain:genmind.ch)",
      "WebFetch(domain:towardsdatascience.com)",
      "WebFetch(domain:www.learnpytorch.io)",
      "WebFetch(domain:www.slingacademy.com)",
      "WebFetch(domain:www.eletreby.me)",
      "WebFetch(domain:www.codegenes.net)",
      "WebFetch(domain:pythonguides.com)",
      "WebFetch(domain:www.geeksforgeeks.org)",
      "WebFetch(domain:www.techbuddies.io)",
      "WebFetch(domain:arxiv.org)",
      "WebFetch(domain:deepwiki.com)"
      "WebFetch(domain:iterm2.com)",
      "WebFetch(domain:sw.kovidgoyal.net)",
      "WebFetch(domain:developers.google.com)",
      "WebFetch(domain:libjxl.readthedocs.io)",
      "WebFetch(domain:filesamples.com)",
      "WebFetch(domain:raw.pixls.us)",
      "WebFetch(domain:docs.rs)",
      "WebFetch(domain:libexif.github.io)",
      "WebFetch(domain:exiv2.org)",
      "WebFetch(domain:exiftool.org)",
      "WebFetch(domain:conan.io)",
      "WebFetch(domain:dev.exiv2.org)",
      "WebFetch(domain:www.raw-files.com)",
      "WebFetch(domain:www.rawsamples.ch)",
      "WebFetch(domain:www.libraw.org)"
    ]
  }
}
```
