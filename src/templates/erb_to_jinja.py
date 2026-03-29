"""ERB → Jinja2 template converter.

Handles the conversion of Puppet ERB templates to Ansible Jinja2 templates.
"""
from __future__ import annotations

import re

from src.utils.facts_mapper import map_fact

# ── Ruby method → Jinja2 filter mapping ──────────────────────────────────────

_METHOD_FILTERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\.downcase"),           "| lower"),
    (re.compile(r"\.upcase"),             "| upper"),
    (re.compile(r"\.strip"),              "| trim"),
    (re.compile(r"\.chomp"),              "| trim"),
    (re.compile(r"\.chop"),               "| trim"),
    (re.compile(r"\.empty\?"),            "| length == 0"),
    (re.compile(r"\.nil\?"),              "is not defined"),
    (re.compile(r"\.length"),             "| length"),
    (re.compile(r"\.size"),               "| length"),
    (re.compile(r"\.to_i"),               "| int"),
    (re.compile(r"\.to_f"),               "| float"),
    (re.compile(r"\.to_s"),               "| string"),
    (re.compile(r"\.sort"),               "| sort"),
    (re.compile(r"\.uniq"),               "| unique"),
    (re.compile(r"\.flatten"),            "| flatten"),
    (re.compile(r"\.compact"),            "| reject('none')"),
    (re.compile(r"\.reverse"),            "| reverse"),
    (re.compile(r"\.first"),              "| first"),
    (re.compile(r"\.last"),               "| last"),
    (re.compile(r"\.keys"),               "| list"),
    (re.compile(r"\.values"),             ".values() | list"),
    (re.compile(r"\.join\('([^']*)'\)"),  r"| join('\1')"),
    (re.compile(r'\.join\("([^"]*)"\)'),  r'| join("\1")'),
    (re.compile(r"\.split\('([^']*)'\)"), r"| split('\1')"),
    (re.compile(r'\.split\("([^"]*)"\)'), r'| split("\1")'),
    (re.compile(r"\.include\?"),          "is contains"),
    (re.compile(r"\.gsub\(/(.*?)/, '(.*?)'\)"), r"| regex_replace('\1', '\2')"),
    (re.compile(r"\.gsub\(/(.*?)/, \"(.*?)\"\)"), r'| regex_replace("\1", "\2")'),
    (re.compile(r"\.match\(/(.*?)/\)"),   r"is match('\1')"),
]

# ── Puppet variable → Ansible fact mapping ────────────────────────────────────

def _map_variable(puppet_var: str) -> str:
    """Map a Puppet template variable to its Ansible equivalent.

    @variable → variable (remove @)
    @fqdn     → ansible_fqdn
    scope['module::param'] → module_param
    """
    var = puppet_var.lstrip("@")

    # scope['module::param'] or @scope['module::param']
    scope_match = re.match(r"scope\['(.+?)'\]", var)
    if scope_match:
        puppet_key = scope_match.group(1)
        return puppet_key.replace("::", "_")

    # Try to map as a Puppet fact
    fact_mapped = map_fact(f"${var}")
    if not fact_mapped.startswith("UNMAPPED"):
        return fact_mapped

    # Convert :: to _ (module::param → module_param)
    return var.replace("::", "_")


class ErbConverter:
    """Converts an ERB template string to Jinja2 format."""

    def convert(self, erb_content: str) -> ConversionOutput:
        """Convert ERB template text to Jinja2.

        Returns a ConversionOutput with the converted content and any warnings.
        """
        output = ConversionOutput()
        lines = erb_content.split("\n")
        result_lines = []

        for i, line in enumerate(lines, start=1):
            try:
                converted = self._convert_line(line, output)
                result_lines.append(converted)
            except Exception as exc:
                output.warnings.append(f"Line {i}: conversion error — {exc}. Original kept.")
                result_lines.append(line)

        output.content = "\n".join(result_lines)
        return output

    def convert_file(self, erb_path: str, jinja_path: str) -> ConversionOutput:
        """Read an ERB file, convert it, write Jinja2 output."""
        from pathlib import Path
        erb_text = Path(erb_path).read_text(encoding="utf-8")
        result = self.convert(erb_text)
        Path(jinja_path).write_text(result.content, encoding="utf-8")
        return result

    # ── Per-line conversion ───────────────────────────────────────────────────

    def _convert_line(self, line: str, output: ConversionOutput) -> str:
        """Convert a single line of ERB to Jinja2."""
        # Process all ERB tags in the line
        result = ""
        pos = 0

        while pos < len(line):
            # Find next ERB open tag
            tag_start = line.find("<%", pos)
            if tag_start == -1:
                result += line[pos:]
                break

            # Append text before the tag
            result += line[pos:tag_start]

            # Find closing tag
            tag_end = line.find("%>", tag_start + 2)
            if tag_end == -1:
                # Unclosed tag — append rest as-is
                output.warnings.append(f"Unclosed ERB tag in: {line.strip()}")
                result += line[tag_start:]
                pos = len(line)
                continue

            tag_content = line[tag_start + 2:tag_end]
            tag_suffix  = line[tag_end + 2:tag_end + 3]  # check for -%>

            # Handle -%> (strip trailing newline)
            if line[tag_end - 1] == "-":
                tag_content = tag_content[:-1] if tag_content.endswith("-") else tag_content

            result += self._convert_tag(tag_content, output)
            pos = tag_end + 2

        return result

    def _convert_tag(self, tag_content: str, output: ConversionOutput) -> str:
        """Convert the content of a single ERB tag."""
        content = tag_content.strip()

        # Comment tag <%# ... %> → {# ... #}
        if content.startswith("#"):
            return "{# " + content[1:].strip() + " #}"

        # Expression tag <%= ... %> → {{ ... }}
        if tag_content.lstrip().startswith("="):
            expr = content.lstrip("=").strip()
            return "{{ " + self._convert_expression(expr, output) + " }}"

        # Block tags <% ... %>
        return self._convert_block(content, output)

    def _convert_expression(self, expr: str, output: ConversionOutput) -> str:
        """Convert a Ruby expression to a Jinja2 expression."""
        # Apply method filter conversions
        result = expr
        for pattern, replacement in _METHOD_FILTERS:
            result = pattern.sub(replacement, result)

        # Convert variables @foo → foo (mapped)
        result = re.sub(
            r"@([a-zA-Z_][a-zA-Z0-9_]*(?:::[a-zA-Z_][a-zA-Z0-9_]*)*)",
            lambda m: _map_variable(m.group(0)),
            result,
        )

        # scope['module::key'] → variable
        result = re.sub(
            r"scope\[(['\"])(.+?)\1\]",
            lambda m: m.group(2).replace("::", "_"),
            result,
        )

        # String interpolation within expression: "#{var}" → "{{ var }}"
        result = re.sub(r'#\{([^}]+)\}', lambda m: "{{ " + _map_variable(m.group(1)) + " }}", result)

        return result

    def _convert_block(self, content: str, output: ConversionOutput) -> str:
        """Convert a Ruby block statement to Jinja2 block tag."""
        stripped = content.strip()

        # end → close the last open block
        if stripped == "end":
            return self._close_block(output)

        # if condition
        m = re.match(r"^if\s+(.+)$", stripped)
        if m:
            cond = self._convert_condition(m.group(1), output)
            output.block_stack.append("if")
            return "{%- if " + cond + " %}" if stripped.endswith("-") else "{% if " + cond + " %}"

        # elsif condition
        m = re.match(r"^elsif\s+(.+)$", stripped)
        if m:
            cond = self._convert_condition(m.group(1), output)
            return "{% elif " + cond + " %}"

        # else
        if stripped == "else":
            return "{% else %}"

        # unless condition
        m = re.match(r"^unless\s+(.+)$", stripped)
        if m:
            cond = self._convert_condition(m.group(1), output)
            output.block_stack.append("if")
            return "{% if not (" + cond + ") %}"

        # array.each do |var|
        m = re.match(r"^(\S+)\.each\s+do\s+\|([^|]+)\|$", stripped)
        if m:
            collection = self._convert_expression(m.group(1), output)
            var_name   = m.group(2).strip().lstrip("$")
            output.block_stack.append("for")
            return "{% for " + var_name + " in " + collection + " %}"

        # array.each_with_index do |item, idx|
        m = re.match(r"^(\S+)\.each_with_index\s+do\s+\|([^,|]+),\s*([^|]+)\|$", stripped)
        if m:
            collection = self._convert_expression(m.group(1), output)
            item_var   = m.group(2).strip().lstrip("$")
            _idx_var   = m.group(3).strip()
            output.block_stack.append("for")
            return "{% for " + item_var + " in " + collection + " %}"

        # hash.each do |key, value|
        m = re.match(r"^(\S+)\.each\s+do\s+\|([^,|]+),\s*([^|]+)\|$", stripped)
        if m:
            collection = self._convert_expression(m.group(1), output)
            key_var    = m.group(2).strip().lstrip("$")
            val_var    = m.group(3).strip().lstrip("$")
            output.block_stack.append("for")
            return "{% for " + key_var + ", " + val_var + " in " + collection + ".items() %}"

        # Unrecognized block — add warning and pass through as comment
        output.warnings.append(f"Unrecognized ERB block: '{stripped}' — kept as comment")
        return "{# ERB: " + stripped + " #}"

    def _convert_condition(self, cond: str, output: ConversionOutput) -> str:
        """Convert a Ruby condition expression to Jinja2."""
        result = cond.strip()

        # Replace Ruby == with Jinja2 ==
        result = result.replace(" == ", " == ")  # same

        # Ruby: !var.nil? → var is defined
        result = re.sub(r"!(\w+)\.nil\?", r"\1 is defined", result)
        result = re.sub(r"(\w+)\.nil\?", r"\1 is not defined", result)

        # Convert expressions
        result = self._convert_expression(result, output)

        return result

    def _close_block(self, output: ConversionOutput) -> str:
        """Close the last open block."""
        if not output.block_stack:
            output.warnings.append("Unexpected 'end' — no open block to close")
            return "{# end #}"
        block_type = output.block_stack.pop()
        return "{% endif %}" if block_type == "if" else "{% endfor %}"


class ConversionOutput:
    """Output of an ERB → Jinja2 conversion."""

    def __init__(self) -> None:
        self.content:    str       = ""
        self.warnings:   list[str] = []
        self.block_stack: list[str] = []  # tracks open if/for blocks
