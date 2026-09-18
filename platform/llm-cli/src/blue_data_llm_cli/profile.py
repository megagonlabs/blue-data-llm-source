"""``blue-llmdb profile`` — the profile command group, over the local config engine.

A deliberate near-copy of `blue_data_user_cli.profile` and `blue_data_kb_cli.profile`: the
CLI checklist requires `profile` to behave identically across the family, so the three are
meant to read the same, and a fix to one belongs in all of them. The engine underneath
(spec, `.profiles` I/O, precedence, secrets) is `configstore` here and
`blue_data.utils.config` there; this module is the Click layer over it and holds nothing
reusable but the prompting.

Two deliberate differences from the siblings, both required by the compliance checklist:
`--format` takes all four values rather than two, and a refusal is this CLI's coded error
rather than a bare `ClickException`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
from rich.console import Console

from blue_data_llm_cli import config, formats
from blue_data_llm_cli.configstore import (
    MASK,
    ConfigResolver,
    Parameter,
    ProfileError,
    ProfileStore,
    SettingError,
    Spec,
    base_spec,
    display,
    encode,
    load_spec,
)

# Markup off: a profile name or a stored value is the user's text, not a style tag.
console = Console(markup=False, highlight=False)

_SPEC_PATH = Path(__file__).with_name("profile_spec.json")


def cli_spec() -> Spec:
    """The family-wide parameters plus this CLI's own.

    The cache directory's default is filled in here rather than written into the spec file.
    It lives under the CLI home, and the home is an environment setting — a cache that
    stayed in `~/.blue-llmdb` after the home moved would be a directory nobody asked for.
    """
    spec = base_spec().merged_with(load_spec(_SPEC_PATH))
    return spec.with_default(
        "BLUE_DATA_LLM_SOURCE_CACHE_DIR", str(Path(config.cli_home()) / "cache")
    )


def profile_store(home: str) -> ProfileStore:
    return ProfileStore(home)


def resolver_for(home: str, profile: str | None = None) -> ConfigResolver:
    return ConfigResolver(cli_spec(), profile_store(home), profile=profile)


def _target(obj: dict[str, Any], name: str | None) -> str:
    """The profile a command acts on: the named one, else the selected one."""
    return name or str(obj["profile"])


def _prompt_for(param: Parameter, current: str | None, walking: bool = False) -> str | None:
    """Ask for one parameter's value, honouring its cast.

    Returns None when the user accepts an empty value, which leaves the parameter unset
    rather than storing an empty string. When there is nothing to read from — a pipeline, a
    CI job — the prompt is not a place to block, so it becomes a usage error naming the
    non-interactive form instead.
    """
    label = param.prompt or param.name
    try:
        if param.is_secret:
            shown = display(current) if current else "(unset)"
            console.print(f"{label} [{shown}]")
            raw = click.prompt(
                "  value (blank keeps current; $VAR stores a reference)",
                default="",
                show_default=False,
                hide_input=True,
            )
            return raw or None
        answer = click.prompt(
            f"{label}", default=current if current is not None else "", show_default=True
        )
        return answer or None
    except click.Abort:
        raise config.CliError(config.USAGE, _non_interactive_advice(param, walking)) from None


def _non_interactive_advice(param: Parameter, walking: bool) -> str:
    if walking:
        return (
            f"there is nothing to read answers from. Set parameters one at a time with "
            f"`{config.PROGRAM} profile config PARAM VALUE`. A secret has to be typed at a "
            f"prompt, or entered there as `$VAR` to reference an environment variable."
        )
    if param.is_secret:
        return (
            f"{param.name} is a secret and there is no terminal to type it into. Run this "
            f"where one can answer, or store a reference by running "
            f"`{config.PROGRAM} profile config {param.name}` interactively once and entering "
            f"`$VAR`."
        )
    return (
        f"{param.name} needs a value and there is nothing to read from. Pass it directly: "
        f"`{config.PROGRAM} profile config {param.name} VALUE`."
    )


def _store_value(param: Parameter, value: str) -> str:
    """The form a value takes on disk — secrets encoded, everything else cast."""
    if param.is_secret:
        return encode(value)
    return str(param.cast_value(value))


# ------------------------------------------------------------------ #
# blue-llmdb profile                                                  #
# ------------------------------------------------------------------ #


@click.group("profile", cls=config.Group)
def profile_group() -> None:
    """Manage profiles and configuration."""


@profile_group.command("add")
@click.argument("name")
@click.pass_obj
def profile_add(obj: dict[str, Any], name: str) -> None:
    """Create a new profile."""
    with config.reporting("table"):
        config.start(obj)
        profile_store(obj["home"]).add(name)
    console.print(f"Created profile: {name}", style="bold")
    console.print(
        f"Configure it with `{config.PROGRAM} profile config` "
        f"(add `--profile {name}` if it is not the selected one).",
        style="dim",
    )


@profile_group.command("remove")
@click.argument("name")
@click.option("--confirm", is_flag=True, default=False, help="Skip the confirmation prompt.")
@click.pass_obj
def profile_remove(obj: dict[str, Any], name: str, confirm: bool) -> None:
    """Delete a profile and its configuration."""
    with config.reporting("table"):
        config.start(obj)
        store = profile_store(obj["home"])
        if not store.exists(name):
            raise config.CliError(
                config.PROFILE,
                f"no such profile: {name!r}. Run `{config.PROGRAM} profile list` to see them.",
            )
        if not confirm:
            try:
                approved = click.confirm(f"Remove profile {name!r} and all of its configuration?")
            except click.Abort:
                raise config.CliError(
                    config.USAGE,
                    f"there is nothing to read an answer from. Pass --confirm to remove "
                    f"{name!r} without asking.",
                ) from None
            if not approved:
                # A no-op, not a failure. Click's own abort exits 1, which this CLI reserves
                # for INTERNAL, and prints `Aborted!` where every other refusal prints
                # `ERROR: <CODE>`. Nothing went wrong: the question was asked and answered.
                console.print(f"Kept profile: {name}", style="dim")
                return
        was_selected = store.selected() == name
        store.remove(name)
        selection = store.selected()
    console.print(f"Removed profile: {name}", style="bold")
    if was_selected:
        console.print(
            f"It was the selected profile; the selection is now {selection!r}.", style="dim"
        )


@profile_group.command("list")
@config.format_option
@click.pass_obj
def profile_list(obj: dict[str, Any], output_format: str | None) -> None:
    """List profiles."""
    published = config.Output(output_format)
    with config.reporting(published):
        fmt = published.resolve(config.start(obj))
        store = profile_store(obj["home"])
        names = store.list_profiles()
        selected = store.selected()
        rows = [
            {
                "Profile": name,
                "Selected": "*" if name == selected else "",
                "Configured": len(store.values(name)),
            }
            for name in names
        ]
        result = formats.tabular(
            ("Profile", "Selected", "Configured"),
            rows,
            {"selected": selected, "profiles": names},
            sentences=(_profiles_sentence(names, selected),),
            empty=f"(no profiles — run `{config.PROGRAM} profile add NAME`)",
        )
    formats.render(result, fmt)


def _profiles_sentence(names: list[str], selected: str) -> str:
    if not names:
        return f"No profiles yet. Run `{config.PROGRAM} profile add NAME` to make one."
    listed = ", ".join(names)
    return f"{len(names)} profile(s): {listed}. The selected one is {selected}."


@profile_group.command("select")
@click.argument("name")
@click.pass_obj
def profile_select(obj: dict[str, Any], name: str) -> None:
    """Set the default profile."""
    with config.reporting("table"):
        config.start(obj)
        try:
            profile_store(obj["home"]).select(name)
        except ProfileError as error:
            raise config.CliError(
                config.PROFILE,
                f"{error}. Run `{config.PROGRAM} profile list` to see them.",
            ) from None
    console.print(f"Selected profile: {name}", style="bold")


@profile_group.command("inspect")
@click.argument("name", required=False)
@config.format_option
@click.pass_obj
def profile_inspect(obj: dict[str, Any], name: str | None, output_format: str | None) -> None:
    """Show configuration values and where each came from.

    Secrets are masked; a `$VAR` reference is shown by name, with whether it currently
    resolves, so you can see what to export. The source is one of `environment`,
    `profile:<name>`, `default` or `unset` — never `flag`, because a command option never
    reaches the resolver.
    """
    published = config.Output(output_format)
    with config.reporting(published):
        root = config.start(obj)
        fmt = published.resolve(root)
        target = _target(obj, name)
        store = profile_store(obj["home"])
        if not store.exists(target):
            # Reporting a full table of defaults under a name that does not exist reads as
            # "here is that profile", which is the one thing it is not.
            raise config.CliError(
                config.PROFILE,
                f"no such profile: {target!r}. Run `{config.PROGRAM} profile list` to see them.",
            )
        spec = cli_spec()
        # Reuse the root group's resolver when reporting on the active profile: it
        # snapshotted the environment before applying the profile, so `inspect` attributes
        # values to the tier that really set them rather than to the exports the CLI made.
        res = (
            root
            if target == obj["profile"]
            else ConfigResolver(spec, store, profile=target, environment=root.environment)
        )
        stored = res.profile_values()

        rows = [
            {
                "Parameter": param.name,
                "Value": _shown(res, param, stored),
                "Source": res.source_of(param.name),
            }
            for param in spec
        ]
        result = formats.tabular(
            ("Parameter", "Value", "Source"),
            rows,
            {
                "profile": target,
                "values": {
                    row["Parameter"]: {"value": row["Value"], "source": row["Source"]}
                    for row in rows
                },
            },
            sentences=(_inspect_sentence(target, rows),),
            title=f"profile: {target}",
        )
    formats.render(result, fmt)


def _shown(res: ConfigResolver, param: Parameter, stored: dict[str, str]) -> str:
    if not param.is_secret:
        value = res.get(param.name)
        return "(unset)" if value is None else str(value)
    # Describe the tier that actually won. Showing the profile's stored form while reporting
    # "environment" as the source would contradict itself — an exported variable outranks a
    # profile that references one.
    source = res.source_of(param.name)
    raw = stored.get(param.name)
    if source == "environment":
        return MASK
    if raw is not None:
        # Whatever the profile holds, including a reference that does not currently resolve:
        # naming the variable is how the user learns what to export.
        return display(raw)
    return "(unset)" if source == "unset" else MASK


def _inspect_sentence(target: str, rows: list[dict[str, Any]]) -> str:
    set_here = sum(1 for row in rows if row["Source"] != "unset")
    return f"Profile {target} resolves {set_here} of {len(rows)} parameters."


@profile_group.command("config")
@click.argument("param", required=False)
@click.argument("value", required=False)
@click.option(
    "--profile",
    "profile_name",
    default=None,
    help="Profile to configure. Default: the selected one.",
)
@click.pass_obj
def profile_config(
    obj: dict[str, Any], param: str | None, value: str | None, profile_name: str | None
) -> None:
    """Set configuration values.

    \b
    blue-llmdb profile config                  walk through every required parameter
    blue-llmdb profile config PARAM            prompt for one parameter
    blue-llmdb profile config PARAM VALUE      set one parameter directly

    Secrets are never taken as VALUE on the command line — a secret parameter always
    prompts, so it stays out of shell history. Enter `$VAR` at the prompt to store a
    reference to an environment variable instead of the secret itself.
    """
    with config.reporting("table"):
        config.start(obj)
        target = _target(obj, profile_name)
        store = profile_store(obj["home"])
        spec = cli_spec()

        # The target is created by the write that needs it, not before. Creating it up
        # front meant `profile config --profile typo NOT_A_PARAM x` refused the parameter
        # with `typo` already made and left behind: a command that changed nothing it was
        # asked to change, and one thing it was not.
        existed = store.exists(target)
        stored = store.values(target)

        if param is None:
            params = spec.required()
            console.print(
                f"Configuring profile {target} "
                f"({len(params)} required parameters; blank keeps the current value).\n"
            )
        else:
            try:
                params = [spec[param]]
            except SettingError as error:
                raise config.CliError(config.PROFILE, str(error)) from None

        if value is not None:
            one = params[0]
            if one.is_secret:
                raise config.CliError(
                    config.PROFILE,
                    f"{one.name} is a secret and cannot be set on the command line, where it "
                    f"would land in shell history. Run `{config.PROGRAM} profile config "
                    f"{one.name}` and enter it at the prompt, or enter `$VAR` there to "
                    f"reference an environment variable.",
                )
            store.set_value(target, one.name, _store_value(one, value), create=True)
            _report_creation(existed, target)
            console.print(f"Set {one.name} in profile {target}", style="bold")
            return

        changed = 0
        for one in params:
            answer = _prompt_for(one, stored.get(one.name), walking=param is None)
            if answer is None:
                continue
            try:
                store.set_value(target, one.name, _store_value(one, answer), create=True)
            except SettingError as error:
                console.print(f"{error} (skipped)", style="red")
                continue
            changed += 1

        _report_creation(existed or not changed, target)
        console.print(
            f"\nUpdated {changed} parameter(s) in profile {target}." if changed else "\nNo changes."
        )


def _report_creation(existed: bool, target: str) -> None:
    if not existed:
        console.print(f"Created profile {target!r}.", style="dim")
