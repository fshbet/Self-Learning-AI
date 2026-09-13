import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Domain } from "./api";

type Ctx = {
  domains: Domain[];
  domain: string;
  setDomain: (d: string) => void;
  current: Domain | undefined;
  loading: boolean;
};

const DomainContext = createContext<Ctx | null>(null);
const KEY = "kp.domain";

export function DomainProvider({ children }: { children: ReactNode }) {
  const { data, isLoading } = useQuery({ queryKey: ["domains"], queryFn: api.domains, refetchInterval: 30000 });
  const [domain, setDomainState] = useState<string>(() => {
    try {
      return localStorage.getItem(KEY) ?? "";
    } catch {
      return "";
    }
  });
  const domains = useMemo(() => (data ?? []).filter((d) => d.loaded), [data]);

  useEffect(() => {
    if (!domains.length) return;
    if (!domains.some((d) => d.id === domain)) {
      const preferred = domains.find((d) => d.id !== "example") ?? domains[0];
      setDomainState(preferred.id);
    }
  }, [domains, domain]);

  const setDomain = (d: string) => {
    setDomainState(d);
    try {
      localStorage.setItem(KEY, d);
    } catch {
      /* ignore */
    }
  };

  const current = domains.find((d) => d.id === domain);
  return (
    <DomainContext.Provider value={{ domains, domain, setDomain, current, loading: isLoading }}>
      {children}
    </DomainContext.Provider>
  );
}

export function useDomain(): Ctx {
  const ctx = useContext(DomainContext);
  if (!ctx) throw new Error("useDomain outside provider");
  return ctx;
}
