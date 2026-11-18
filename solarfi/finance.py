"""Küçük, bağımlılıksız finansal fonksiyon seti.

`numpy-financial` yerine bunları kullanıyoruz çünkü:

* IRR'nin çözülemediği durumda ne döneceğini biz kontrol ediyoruz
  (sessizce ``nan`` dönüp arayüzde "nan%" yazmasındansa ``None``).
* Zaman indeksleme konusundaki varsayım tek bir yerde, açıkça yazılı.

**Zaman konvansiyonu:** Bütün fonksiyonlarda ``cashflows[i]`` i. dönemin
*sonundaki* nakit akışıdır ve ``i`` numaralı dönem ``(1 + r) ** i`` ile
iskonto edilir. Yani ``cashflows[0]`` t=0'dadır ve iskonto edilmez.
Bu, Excel'in ``XNPV``/``NPV`` davranışından farklıdır; Excel'in ``NPV``
fonksiyonu ilk elemanı da iskonto eder.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["npv", "irr", "pmt", "discount_factors", "payback_period"]


def discount_factors(rate: float, periods: int) -> list[float]:
    """t=0..periods-1 için iskonto faktörleri."""
    if rate <= -1.0:
        raise ValueError("İskonto oranı -100%'den büyük olmalı.")
    return [1.0 / ((1.0 + rate) ** i) for i in range(periods)]


def npv(rate: float, cashflows: Sequence[float]) -> float:
    """Net bugünkü değer. ``cashflows[0]`` iskonto edilmez (t=0)."""
    if not len(cashflows):
        return 0.0
    return sum(cf * df for cf, df in zip(cashflows, discount_factors(rate, len(cashflows)), strict=True))


def pmt(rate: float, nper: int, principal: float) -> float:
    """Eşit taksitli (anüite) kredi geri ödemesi. Pozitif değer döner."""
    if nper <= 0:
        return 0.0
    if principal == 0:
        return 0.0
    if rate == 0:
        return principal / nper
    factor = (1.0 + rate) ** nper
    return principal * rate * factor / (factor - 1.0)


def irr(cashflows: Sequence[float], lo: float = -0.9999, hi: float = 10.0) -> float | None:
    """İç verim oranı; çözüm yoksa ``None``.

    Önce ``[lo, hi]`` aralığında işaret değişimi arar, sonra ikiye bölme
    (bisection) uygular. Newton yerine bisection tercih edildi çünkü
    yakınsaması garanti ve proje nakit akışlarında başlangıç tahminine
    duyarlı değil.

    Not: Nakit akışı birden fazla kez işaret değiştiriyorsa (örneğin sonda
    büyük bir söküm maliyeti varsa) matematiksel olarak birden çok IRR
    olabilir. Bu fonksiyon bulduğu ilk kökü döner; böyle durumlarda IRR
    yerine NPV'ye bakmak daha doğrudur.
    """
    flows = list(cashflows)
    if len(flows) < 2:
        return None
    if all(cf >= 0 for cf in flows) or all(cf <= 0 for cf in flows):
        return None  # işaret değişimi yok, kök yok

    def f(r: float) -> float:
        return npv(r, flows)

    steps = 400
    prev_r = lo
    prev_v = f(prev_r)
    for i in range(1, steps + 1):
        r = lo + (hi - lo) * i / steps
        v = f(r)
        if prev_v == 0.0:
            return prev_r
        if (prev_v > 0) != (v > 0):
            a, b = prev_r, r
            for _ in range(200):
                mid = (a + b) / 2.0
                vm = f(mid)
                if vm == 0.0:
                    return mid
                if (f(a) > 0) != (vm > 0):
                    b = mid
                else:
                    a = mid
                if abs(b - a) < 1e-12:
                    break
            return (a + b) / 2.0
        prev_r, prev_v = r, v
    return None


def payback_period(cashflows: Sequence[float], discount_rate: float | None = None) -> float | None:
    """Kümülatif nakit akışının sıfırı geçtiği dönem (doğrusal interpolasyonlu).

    ``discount_rate`` verilirse iskontolu geri ödeme süresi hesaplanır.
    Hiç pozitife geçmiyorsa ``None`` döner.
    """
    flows = list(cashflows)
    if discount_rate is not None:
        flows = [cf * df for cf, df in zip(flows, discount_factors(discount_rate, len(flows)), strict=True)]
    cum = 0.0
    prev_cum = 0.0
    for i, cf in enumerate(flows):
        prev_cum = cum
        cum += cf
        if cum >= 0 and i > 0:
            if cf == 0:
                return float(i)
            return (i - 1) + (-prev_cum / cf)
    return None
