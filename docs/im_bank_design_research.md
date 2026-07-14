# iM Bank Color System Research

## Sources reviewed

- iM뱅크 Google Play listing and screenshots: https://play.google.com/store/apps/details?hl=ko&id=kr.co.dgb.dgbm
- iM뱅크 App Store listing: https://apps.apple.com/kr/app/im%EB%B1%85%ED%81%AC-%EC%95%84%EC%9D%B4%EC%97%A0%EB%B1%85%ED%81%AC-%EB%AA%A8%EB%B0%94%EC%9D%BC%EB%B1%85%ED%82%B9/id1067748687
- iM뱅크 app download page: https://www.imbank.co.kr/cms/app/imbank_down.html
- iM뱅크 CI page: https://www.imbank.co.kr/cms/dgi/sdd_4/sdd_41/1187587_1663.html
- HeraldK article on the iM Bank CI renewal: https://heraldk.com/2024/09/14/%EC%83%88%EB%A1%9C-%ED%83%9C%EC%96%B4%EB%82%98%EB%8A%94-%EC%95%84%EC%9D%B4%EC%97%A0%EB%B1%85%ED%81%AC%E2%80%A6%E2%80%98%EB%AF%BC%ED%8A%B8%E2%80%99%EC%83%89-%EB%A1%9C%EA%B3%A0%EB%A1%9C-mz%EC%84%B8/

## Observations

iM뱅크의 현재 앱/브랜드 인상은 강한 민트, 연한 민트 그레이, 라임 포인트의 조합이다. Google Play 스크린샷에서는 첫 장의 전체 배경이 강한 민트로 쓰이고, 이후 화면에서는 연한 민트 그레이 배경 위에 흰색 모바일 UI를 올리는 구조가 반복된다.

HeraldK 기사에서는 iM뱅크 관계자가 "민트와 라임색"으로 정체성과 차별성을 노린다고 설명한다. 로고 역시 기존 DGB 심볼의 계승과 새싹/파랑새 날개 이미지를 함께 담는다는 설명이 있어, 색상은 보수적인 금융 블루보다 더 젊고 디지털 친화적인 인상을 만드는 역할로 해석했다.

## Dark mode check

Public search and the official app listings did not surface a clear iM뱅크 dark-mode announcement or release note. The Google Play and App Store descriptions focus on convenience, transfers, benefits, iM-i, exchange, and group-account features rather than theme options.

However, the first Google Play screenshot includes an in-app screen using a deep teal/charcoal background with mint interface elements. For this POC, dark mode is therefore treated as an inferred iM-style dark theme: preserve the brand mint, move large surfaces to charcoal, and keep cards slightly lifted for an operational finance-console feel.

## Extracted colors

Colors were sampled from the provided iM증권 logo and app screenshots. The current database console intentionally uses a monochrome base plus the sampled logo mint only; lime is recorded as a brand-adjacent color but not used in the UI accent system.

| Role | Hex | Source/Use |
| --- | --- | --- |
| Primary mint | `#00C4A8` | Provided logo symbol dominant sample |
| Hover mint | `#00D0B0` | Slightly brighter interactive state |
| Lime accent | `#86DC79` | Provided logo gradient/accent |
| Deep teal | `#05463A` | Dark app UI and readable action color |
| Mid green | `#26A073` | Secondary product card tone |
| Pale mint surface | `#F5F8F7` | App screenshot neutral background |
| Mint wash | `#DCECEA` | App screenshot background band |
| Soft mint panel | `#EAF4F1` | Derived UI surface state |

## Applied UI rules

- Use the user's requested monochrome SaaS console as the dominant surface system.
- Use primary mint for status, validation, active states, primary actions, and focus rings.
- Avoid secondary brand accents in this POC so the screen stays closer to Outerbase's restrained database-console feel.
- Keep neutral white/gray surfaces dominant so the tool feels operational rather than promotional.
- In dark mode, use charcoal surfaces inspired by the darker app screenshot, with mint action states and light text for contrast.
