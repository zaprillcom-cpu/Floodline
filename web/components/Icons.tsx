type IconProps = { active?: boolean };

export function IconEscape({ active }: IconProps) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M12 3.5 4.5 9.2V20h5.2v-5.2h4.6V20H19.5V9.2L12 3.5Z"
        fill={active ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function IconReport({ active }: IconProps) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M12 4.2 20 18.5H4L12 4.2Z"
        fill={active ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path d="M12 10v4.2" stroke={active ? "#000" : "currentColor"} strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="12" cy="16.2" r="0.8" fill={active ? "#000" : "currentColor"} />
    </svg>
  );
}

export function IconLive({ active }: IconProps) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="2.2" fill="currentColor" />
      <path
        d="M7.2 7.2a6.8 6.8 0 0 0 0 9.6M16.8 7.2a6.8 6.8 0 0 1 0 9.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        opacity={active ? 1 : 0.85}
      />
    </svg>
  );
}

export function IconMe({ active }: IconProps) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="9" r="3.1" fill={active ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.6" />
      <path
        d="M6.2 19.2c1.2-2.6 3.2-3.8 5.8-3.8s4.6 1.2 5.8 3.8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function IconGraph({ active }: IconProps) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7 7 17 9M7 7l3 10M17 9l-7 8" stroke="currentColor" strokeWidth="1.4" fill="none" />
      {[[7, 7], [17, 9], [10, 17]].map(([x, y]) => (
        <circle key={`${x}-${y}`} cx={x} cy={y} r="2.6" fill={active ? "currentColor" : "#000"} stroke="currentColor" strokeWidth="1.6" />
      ))}
    </svg>
  );
}
