def apply_team_char_classes(task, chars):
    """Apply custom code only after the complete three-character team is known."""
    if len(chars) != 3 or any(char is None for char in chars):
        return chars
    infos = [char_dict.get(char.char_name) for char in chars]
    if any(info is None for info in infos):
        return chars
    builtin_classes = [info['cls'] for info in infos]
    try:
        team = normalize_team(builtin_classes)
    except ValueError:
        return chars
    for index, (char, builtin_cls) in enumerate(zip(chars, builtin_classes)):
        desired_cls = load_team_char_class(builtin_cls, team)
        if type(char) is desired_cls:
            continue
        replacement = desired_cls(
            task,
            char.index,
            char_name=char.char_name,
            confidence=char.confidence,
            ring_index=char.ring_index,
            char_type=char.char_type,
            buff_time=char.buff_time,
        )
        replacement.__dict__.update(char.__dict__)
        chars[index] = replacement
    return chars
