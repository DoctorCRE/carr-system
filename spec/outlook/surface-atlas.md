# Outlook surface atlas

Generated from [surface-atlas.json](surface-atlas.json) by `python3 ops/outlook-atlas.py render`.

Observed build `16.114.26101123`. static declarations, binary URL literals and audited arm64 grammar; runtime behavior untested.

Confidence `proven` applies to the static declaration or literal, never to runtime acceptance.

Binary recovery distinguishes complete ASCII URL literals from audit-guided reconstruction of separate arm64 constants.

Query values are omitted. First seen means first observed in this atlas history, not introduction by Microsoft.

## app intent

```json
{
  "assistantDefinedSchemaTraits": [],
  "assistantDefinedSchemas": [],
  "authenticationPolicy": 0,
  "availabilityAnnotations": {
    "LNPlatformNameWildcard": {
      "introducedVersion": "*"
    }
  },
  "effectiveBundleIdentifiers": [],
  "fullyQualifiedTypeName": "CalendarWidgetExtension.TimelineWidgetAppIntent",
  "identifier": "TimelineWidgetAppIntent",
  "isAuthPolExplicit": false,
  "isDiscoverable": true,
  "mangledTypeName": "23CalendarWidgetExtension08TimelineB9AppIntentV",
  "mangledTypeNameByBundleIdentifier": {},
  "mangledTypeNameByBundleIdentifierV2": {},
  "mangledTypeNameV2": "23CalendarWidgetExtension08TimelineB9AppIntentV",
  "openAppWhenRun": false,
  "outputFlags": 0,
  "parameters": [
    {
      "capabilities": 0,
      "dynamicOptionsSupport": 0,
      "inputConnectionBehavior": 0,
      "isInput": false,
      "isOptional": false,
      "name": "direction",
      "resolvableInputTypes": [
        {
          "kindValue": 0,
          "valueType": {
            "primitive": {
              "wrapper": {
                "typeIdentifier": 7
              }
            }
          }
        },
        {
          "kindValue": 0,
          "valueType": {
            "primitive": {
              "wrapper": {
                "typeIdentifier": 2
              }
            }
          }
        },
        {
          "kindValue": 0,
          "valueType": {
            "primitive": {
              "wrapper": {
                "typeIdentifier": 0
              }
            }
          }
        }
      ],
      "title": {
        "alternatives": [],
        "key": "direction"
      },
      "typeSpecificMetadata": [],
      "valueType": {
        "primitive": {
          "wrapper": {
            "typeIdentifier": 2
          }
        }
      }
    }
  ],
  "presentationStyle": 0,
  "requiredCapabilities": [],
  "supportedModes": 1,
  "systemProtocolMetadata": [],
  "systemProtocolMetadataV2": [],
  "systemProtocols": [],
  "title": {
    "alternatives": [],
    "key": "Timeline Widget App Intent"
  },
  "typeSpecificMetadata": [],
  "visibilityMetadata": {
    "assistantOnly": false,
    "isDiscoverable": true
  }
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/PlugIns/CalendarWidgetExtension.appex/Contents/Resources/Metadata.appintents/extract.actionsdata` at `/actions/TimelineWidgetAppIntent`.

```json
{
  "assistantDefinedSchemaTraits": [],
  "assistantDefinedSchemas": [],
  "authenticationPolicy": 0,
  "availabilityAnnotations": {
    "LNPlatformNameWildcard": {
      "introducedVersion": "*"
    }
  },
  "effectiveBundleIdentifiers": [],
  "fullyQualifiedTypeName": "Microsoft_Outlook.FocusFilterIntent",
  "identifier": "FocusFilterIntent",
  "isAuthPolExplicit": false,
  "isDiscoverable": true,
  "mangledTypeName": "17Microsoft_Outlook17FocusFilterIntentV",
  "mangledTypeNameByBundleIdentifier": {},
  "mangledTypeNameByBundleIdentifierV2": {},
  "mangledTypeNameV2": "17Microsoft_Outlook17FocusFilterIntentV",
  "openAppWhenRun": false,
  "outputFlags": 0,
  "parameters": [
    {
      "capabilities": 0,
      "dynamicOptionsSupport": 2,
      "inputConnectionBehavior": 0,
      "isInput": false,
      "isOptional": true,
      "name": "profile",
      "resolvableInputTypes": [],
      "title": {
        "alternatives": [],
        "key": "Profile"
      },
      "typeSpecificMetadata": [],
      "valueType": {
        "entity": {
          "wrapper": {
            "typeName": "FocusFilterProfile"
          }
        }
      }
    }
  ],
  "presentationStyle": 0,
  "requiredCapabilities": [],
  "supportedModes": 1,
  "systemProtocolMetadata": [
    "com.apple.link.systemProtocol.FocusConfiguration",
    {
      "empty": {}
    }
  ],
  "systemProtocolMetadataV2": [
    "com.apple.link.systemProtocol.FocusConfiguration",
    {
      "empty": {}
    }
  ],
  "systemProtocols": [
    "com.apple.link.systemProtocol.FocusConfiguration"
  ],
  "title": {
    "alternatives": [],
    "key": "Set Profile"
  },
  "typeSpecificMetadata": [],
  "visibilityMetadata": {
    "assistantOnly": false,
    "isDiscoverable": true
  }
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Resources/Metadata.appintents/extract.actionsdata` at `/actions/FocusFilterIntent`.

## document type

```json
{
  "CFBundleTypeName": "Any application",
  "CFBundleTypeOSTypes": [
    "????"
  ],
  "CFBundleTypeRole": "Viewer",
  "LSHandlerRank": "Alternate"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/41`.

```json
{
  "CFBundleTypeName": "Any file",
  "CFBundleTypeOSTypes": [
    "****"
  ],
  "CFBundleTypeRole": "Viewer",
  "LSHandlerRank": "Alternate"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/43`.

```json
{
  "CFBundleTypeExtensions": [
    "msg"
  ],
  "CFBundleTypeIconFile": "M822.icns",
  "CFBundleTypeName": "CDFV2 Microsoft Outlook Message",
  "CFBundleTypeRole": "Editor",
  "DocumentTypeSection": "Disabled",
  "LSHandlerRank": "Default",
  "LSItemContentTypes": [
    "com.microsoft.outlook.msg",
    "dyn.ah62d4rv4ge80465h"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/16`.

```json
{
  "CFBundleTypeName": "Folder",
  "CFBundleTypeOSTypes": [
    "fold"
  ],
  "CFBundleTypeRole": "Viewer",
  "LSHandlerRank": "Default"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/42`.

```json
{
  "CFBundleTypeExtensions": [
    "xls"
  ],
  "CFBundleTypeIconFile": "sLS3.icns",
  "CFBundleTypeName": "Microsoft Excel 3.0 worksheet stationery",
  "CFBundleTypeOSTypes": [
    "sLS3"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None",
  "LSItemContentTypes": [
    "com.microsoft.excel.xls.stationery.biff3"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/4`.

```json
{
  "CFBundleTypeExtensions": [
    "xls"
  ],
  "CFBundleTypeIconFile": "sLS4.icns",
  "CFBundleTypeName": "Microsoft Excel 4.0 worksheet stationery",
  "CFBundleTypeOSTypes": [
    "sLS4"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None",
  "LSItemContentTypes": [
    "com.microsoft.excel.xls.stationery.biff4"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/5`.

```json
{
  "CFBundleTypeExtensions": [
    "xlsb"
  ],
  "CFBundleTypeIconFile": "XLSB.icns",
  "CFBundleTypeName": "Microsoft Excel Binary Workbook (.xlsb)",
  "CFBundleTypeOSTypes": [
    "XLSB"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "Alternate",
  "LSItemContentTypes": [
    "com.microsoft.excel.sheet.binary.macroenabled"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/6`.

```json
{
  "CFBundleTypeExtensions": [
    "xlsm"
  ],
  "CFBundleTypeIconFile": "XLSM.icns",
  "CFBundleTypeName": "Microsoft Excel Macro-Enabled Workbook (.xlsm)",
  "CFBundleTypeOSTypes": [
    "XLSM"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "Alternate",
  "LSItemContentTypes": [
    "org.openxmlformats.spreadsheetml.sheet.macroenabled",
    "com.microsoft.excel.openxmlformats.spreadsheetml.sheet.macroenabled"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/7`.

```json
{
  "CFBundleTypeExtensions": [
    "xltx"
  ],
  "CFBundleTypeIconFile": "XLTX.icns",
  "CFBundleTypeName": "Microsoft Excel Template (.xltx)",
  "CFBundleTypeOSTypes": [
    "XLTX"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "Alternate",
  "LSItemContentTypes": [
    "org.openxmlformats.spreadsheetml.template",
    "com.microsoft.excel.openxmlformats.spreadsheetml.template"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/10`.

```json
{
  "CFBundleTypeExtensions": [
    "xlsx"
  ],
  "CFBundleTypeIconFile": "XLSX.icns",
  "CFBundleTypeName": "Microsoft Excel Workbook (.xlsx)",
  "CFBundleTypeOSTypes": [
    "XLSX"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "Alternate",
  "LSItemContentTypes": [
    "org.openxmlformats.spreadsheetml.sheet",
    "com.microsoft.excel.openxmlformats.spreadsheetml.sheet"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/8`.

```json
{
  "CFBundleTypeExtensions": [
    "xlt"
  ],
  "CFBundleTypeIconFile": "XLT.icns",
  "CFBundleTypeName": "Microsoft Excel Workbook Template (.xlt)",
  "CFBundleTypeOSTypes": [
    "sLS8"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "Alternate",
  "LSItemContentTypes": [
    "com.microsoft.excel.xlt"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/9`.

```json
{
  "CFBundleTypeIconFile": "NewsCache.icns",
  "CFBundleTypeName": "Microsoft Outlook News Cache",
  "CFBundleTypeOSTypes": [
    "Ncch"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/40`.

```json
{
  "CFBundleTypeExtensions": [
    "oft"
  ],
  "CFBundleTypeIconFile": "template.icns",
  "CFBundleTypeName": "Microsoft Outlook Template",
  "CFBundleTypeRole": "Editor",
  "DocumentTypeSection": "Disabled",
  "LSHandlerRank": "Default",
  "LSItemContentTypes": [
    "com.microsoft.outlook.oft"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/17`.

```json
{
  "CFBundleTypeExtensions": [
    "rge"
  ],
  "CFBundleTypeIconFile": "Aarc.icns",
  "CFBundleTypeName": "Microsoft Outlook archive file",
  "CFBundleTypeOSTypes": [
    "Aarc",
    "rge "
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None",
  "LSTypeIsPackage": "Yes"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/24`.

```json
{
  "CFBundleTypeExtensions": [
    "ics"
  ],
  "CFBundleTypeIconFile": "lcs.icns",
  "CFBundleTypeMIMETypes": [
    "text/calendar"
  ],
  "CFBundleTypeName": "Microsoft Outlook calendar event",
  "CFBundleTypeOSTypes": [
    "iCal "
  ],
  "CFBundleTypeRole": "Editor",
  "DocumentTypeSection": "Disabled",
  "LSHandlerRank": "Default",
  "LSItemContentTypes": [
    "com.apple.ical.ics",
    "com.microsoft.outlook15.icalendar"
  ],
  "LSTypeIsPackage": false
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/28`.

```json
{
  "CFBundleTypeExtensions": [
    "vcs"
  ],
  "CFBundleTypeIconFile": "lcs.icns",
  "CFBundleTypeMIMETypes": [
    "text/x-vCalendar"
  ],
  "CFBundleTypeName": "Microsoft Outlook calendar event",
  "CFBundleTypeOSTypes": [
    "vCal "
  ],
  "CFBundleTypeRole": "Viewer",
  "DocumentTypeSection": "Disabled",
  "LSHandlerRank": "Alternate",
  "LSItemContentTypes": [
    "com.apple.ical.vcs"
  ],
  "LSTypeIsPackage": false
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/29`.

```json
{
  "CFBundleTypeExtensions": [
    "vcf"
  ],
  "CFBundleTypeIconFile": "vCrd.icns",
  "CFBundleTypeName": "Microsoft Outlook contact",
  "CFBundleTypeOSTypes": [
    "vCrd"
  ],
  "CFBundleTypeRole": "Editor",
  "DocumentTypeSection": "Disabled",
  "LSHandlerRank": "Default",
  "LSItemContentTypes": [
    "public.vcard"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/25`.

```json
{
  "CFBundleTypeIconFile": "Nwrc.icns",
  "CFBundleTypeName": "Microsoft Outlook database index",
  "CFBundleTypeOSTypes": [
    "Indx"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/12`.

```json
{
  "CFBundleTypeIconFile": "Nwrc.icns",
  "CFBundleTypeName": "Microsoft Outlook database",
  "CFBundleTypeOSTypes": [
    "Nwrc"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/11`.

```json
{
  "CFBundleTypeIconFile": "Nwrc.icns",
  "CFBundleTypeName": "Microsoft Outlook database",
  "CFBundleTypeOSTypes": [
    "OEDB"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/32`.

```json
{
  "CFBundleTypeExtensions": [
    "olk15CalAttach",
    "olk15CalAttach",
    "olk15Category"
  ],
  "CFBundleTypeIconFile": "Nwrc.icns",
  "CFBundleTypeName": "Microsoft Outlook document",
  "CFBundleTypeOSTypes": [
    "Nwrc"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/26`.

```json
{
  "CFBundleTypeIconFile": "CMap.icns",
  "CFBundleTypeName": "Microsoft Outlook document",
  "CFBundleTypeOSTypes": [
    "CMap"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/23`.

```json
{
  "CFBundleTypeIconFile": "Mail.icns",
  "CFBundleTypeName": "Microsoft Outlook document",
  "CFBundleTypeOSTypes": [
    "OECl"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/31`.

```json
{
  "CFBundleTypeIconFile": "PrintFromFile.icns",
  "CFBundleTypeName": "Microsoft Outlook document",
  "CFBundleTypeOSTypes": [
    "PFrm"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/20`.

```json
{
  "CFBundleTypeIconFile": "MailingLists.icns",
  "CFBundleTypeName": "Microsoft Outlook list",
  "CFBundleTypeOSTypes": [
    "List"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/21`.

```json
{
  "CFBundleTypeExtensions": [
    "eml"
  ],
  "CFBundleTypeIconFile": "M822.icns",
  "CFBundleTypeName": "Microsoft Outlook mail message",
  "CFBundleTypeOSTypes": [
    "M822"
  ],
  "CFBundleTypeRole": "Editor",
  "DocumentTypeSection": "Disabled",
  "LSHandlerRank": "Default",
  "LSItemContentTypes": [
    "com.microsoft.outlook15.email-message",
    "com.apple.mail.email"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/15`.

```json
{
  "CFBundleTypeExtensions": [
    "mbox"
  ],
  "CFBundleTypeIconFile": "Mail.icns",
  "CFBundleTypeName": "Microsoft Outlook mailbox",
  "CFBundleTypeOSTypes": [
    "MBOX"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/27`.

```json
{
  "CFBundleTypeExtensions": [
    "olk15Pref"
  ],
  "CFBundleTypeIconFile": "pref.icns",
  "CFBundleTypeName": "Microsoft Outlook preferences",
  "CFBundleTypeOSTypes": [
    "pref"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/13`.

```json
{
  "CFBundleTypeIconFile": "RULE.icns",
  "CFBundleTypeName": "Microsoft Outlook rule file",
  "CFBundleTypeOSTypes": [
    "RULE"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/14`.

```json
{
  "CFBundleTypeExtensions": [
    "olk15Signature"
  ],
  "CFBundleTypeIconFile": "Sigs.icns",
  "CFBundleTypeName": "Microsoft Outlook signatures",
  "CFBundleTypeOSTypes": [
    "Sigs"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/19`.

```json
{
  "CFBundleTypeExtensions": [
    "eragesoundset"
  ],
  "CFBundleTypeIconFile": "SndS.icns",
  "CFBundleTypeName": "Microsoft Outlook sounds",
  "CFBundleTypeRole": "Viewer",
  "LSHandlerRank": "Default"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/22`.

```json
{
  "CFBundleTypeIconFile": "Mail.icns",
  "CFBundleTypeName": "Microsoft Outlook text document",
  "CFBundleTypeOSTypes": [
    "TEXT"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "None"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/30`.

```json
{
  "CFBundleTypeExtensions": [
    "ppt"
  ],
  "CFBundleTypeIconFile": "SLD8.icns",
  "CFBundleTypeName": "Microsoft PowerPoint 97-2003 Presentation",
  "CFBundleTypeOSTypes": [
    "SLD8"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "Alternate",
  "LSItemContentTypes": [
    "com.microsoft.powerpoint.ppt"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/2`.

```json
{
  "CFBundleTypeExtensions": [
    "pptx"
  ],
  "CFBundleTypeIconFile": "PPTX.icns",
  "CFBundleTypeName": "Microsoft PowerPoint Presentation",
  "CFBundleTypeOSTypes": [
    "PPTX"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "Alternate",
  "LSItemContentTypes": [
    "org.openxmlformats.presentationml.presentation",
    "com.microsoft.powerpoint.openxmlformats.presentationml.presentation"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/3`.

```json
{
  "CFBundleTypeExtensions": [
    "doc"
  ],
  "CFBundleTypeIconFile": "W8BN.icns",
  "CFBundleTypeName": "Microsoft Word 97 - 2004 document (.doc)",
  "CFBundleTypeOSTypes": [
    "W8BN"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "Alternate",
  "LSItemContentTypes": [
    "com.microsoft.word.doc"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/0`.

```json
{
  "CFBundleTypeExtensions": [
    "docx"
  ],
  "CFBundleTypeIconFile": "WXBN.icns",
  "CFBundleTypeName": "Microsoft Word document (.docx)",
  "CFBundleTypeOSTypes": [
    "WXBN"
  ],
  "CFBundleTypeRole": "None",
  "LSHandlerRank": "Alternate",
  "LSItemContentTypes": [
    "org.openxmlformats.wordprocessingml.document",
    "com.microsoft.word.openxmlformats.wordprocessingml.document"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/1`.

```json
{
  "CFBundleTypeExtensions": [
    "mime",
    "mme"
  ],
  "CFBundleTypeIconFile": "M822.icns",
  "CFBundleTypeName": "Multipurpose Internet Mail Extensions (MIME) file",
  "CFBundleTypeRole": "Viewer",
  "LSHandlerRank": "Default"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/18`.

```json
{
  "CFBundleTypeExtensions": [
    "emltpl"
  ],
  "CFBundleTypeIconFile": "template.icns",
  "CFBundleTypeName": "emltpl",
  "CFBundleTypeOSTypes": [
    "vTPL"
  ],
  "CFBundleTypeRole": "Editor",
  "DocumentTypeSection": "Disabled",
  "LSHandlerRank": "Default"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/39`.

```json
{
  "CFBundleTypeExtensions": [
    "olk15Contact"
  ],
  "CFBundleTypeIconFile": "vCrd.icns",
  "CFBundleTypeName": "olk15_contact",
  "CFBundleTypeOSTypes": [
    "vCTC"
  ],
  "CFBundleTypeRole": "Viewer",
  "LSHandlerRank": "Default"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/37`.

```json
{
  "CFBundleTypeExtensions": [
    "olk15Event"
  ],
  "CFBundleTypeIconFile": "lcs.icns",
  "CFBundleTypeName": "olk15_event",
  "CFBundleTypeOSTypes": [
    "vCAL"
  ],
  "CFBundleTypeRole": "Viewer",
  "LSHandlerRank": "Default"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/34`.

```json
{
  "CFBundleTypeExtensions": [
    "olk15Group"
  ],
  "CFBundleTypeIconFile": "CMap.icns",
  "CFBundleTypeName": "olk15_group",
  "CFBundleTypeOSTypes": [
    "vGRP"
  ],
  "CFBundleTypeRole": "Viewer",
  "LSHandlerRank": "Default"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/38`.

```json
{
  "CFBundleTypeExtensions": [
    "olk15Message"
  ],
  "CFBundleTypeIconFile": "Mail.icns",
  "CFBundleTypeName": "olk15_message",
  "CFBundleTypeOSTypes": [
    "vMSG"
  ],
  "CFBundleTypeRole": "Viewer",
  "LSHandlerRank": "Default"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/33`.

```json
{
  "CFBundleTypeExtensions": [
    "olk15Note"
  ],
  "CFBundleTypeIconFile": "lcs.icns",
  "CFBundleTypeName": "olk15_note",
  "CFBundleTypeOSTypes": [
    "vNTE"
  ],
  "CFBundleTypeRole": "Viewer",
  "LSHandlerRank": "Default"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/36`.

```json
{
  "CFBundleTypeExtensions": [
    "olk15Task"
  ],
  "CFBundleTypeIconFile": "lcs.icns",
  "CFBundleTypeName": "olk15_task",
  "CFBundleTypeOSTypes": [
    "vTSK"
  ],
  "CFBundleTypeRole": "Viewer",
  "LSHandlerRank": "Default"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleDocumentTypes/35`.

```json
{
  "CFBundleTypeRole": "MDImporter",
  "LSItemContentTypes": [
    "com.microsoft.outlook15.event",
    "com.microsoft.outlook15.contact",
    "com.microsoft.outlook15.message",
    "com.microsoft.outlook15.note",
    "com.microsoft.outlook15.task"
  ]
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Library/Spotlight/Microsoft Outlook Spotlight Importer.mdimporter/Contents/Info.plist` at `/CFBundleDocumentTypes/0`.

## plugin

```json
{
  "CFBundleIdentifier": "com.microsoft.Outlook.CalendarWidget",
  "CFBundleVersion": "16.114.26101123",
  "NSExtension": {
    "NSExtensionPointIdentifier": "com.apple.widgetkit-extension"
  }
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/PlugIns/CalendarWidgetExtension.appex/Contents/Info.plist` at ``.

## supported intent

```json
"INSendMessageIntent"
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/INIntentsSupported/1`.

```json
"INShareFocusStatusIntent"
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/INIntentsSupported/0`.

## url literal

```json
{
  "host": "emails",
  "path": "/message/open",
  "query_keys": [
    "immutableID"
  ],
  "scheme": "ms-outlook"
}
```

First seen `16.114.26101123`. literal grammar only; runtime acceptance unknown.

- `Contents/Frameworks/WordMail.framework/Versions/A/WordMail` at `byte 69566144, length 45`.
- `Contents/Frameworks/WordMail.framework/Versions/A/WordMail` at `byte 156191872, length 45`.

## url query vocabulary

```json
{
  "host": "emails",
  "query_keys": [
    "account",
    "attachments",
    "bcc",
    "body",
    "cc",
    "from",
    "immutableID",
    "signature",
    "subject",
    "to"
  ],
  "runtime_acceptance": "untested",
  "scheme": "ms-outlook"
}
```

First seen `16.114.26101123`. audited arm64 constants and control flow; runtime acceptance untested.

- `Contents/MacOS/Microsoft Outlook` at `byte 143402304, length 236`. Audit `grammar` evidence `email_keys`.

```json
{
  "host": "events",
  "query_keys": [
    "account",
    "attendees",
    "calendar",
    "date",
    "end",
    "iCal",
    "immutableID",
    "instanceID",
    "isallday",
    "location",
    "restID",
    "seriesMasterID",
    "start",
    "title",
    "view"
  ],
  "runtime_acceptance": "untested",
  "scheme": "ms-outlook"
}
```

First seen `16.114.26101123`. audited arm64 constants and control flow; runtime acceptance untested.

- `Contents/MacOS/Microsoft Outlook` at `byte 143406324, length 364`. Audit `grammar` evidence `event_keys`.

## url reconstructed

```json
{
  "host": "emails",
  "path": "/new",
  "query_keys": [
    "account",
    "attachments",
    "bcc",
    "body",
    "cc",
    "from",
    "signature",
    "subject",
    "to"
  ],
  "runtime_acceptance": "untested",
  "scheme": "ms-outlook",
  "static_dispatch": "parsed_forwarded"
}
```

First seen `16.114.26101123`. audited arm64 constants and control flow; runtime acceptance untested.

- `Contents/MacOS/Microsoft Outlook` at `byte 157264664, length 100`. Audit `grammar` evidence `attachment_paths`.
- `Contents/MacOS/Microsoft Outlook` at `byte 143403140, length 364`. Audit `grammar` evidence `email_account_decimal`.
- `Contents/MacOS/Microsoft Outlook` at `byte 143402840, length 32`. Audit `grammar` evidence `email_account_parse`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157265064, length 316`. Audit `grammar` evidence `email_accounts`.
- `Contents/MacOS/Microsoft Outlook` at `byte 143402304, length 236`. Audit `grammar` evidence `email_keys`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157269284, length 224`. Audit `grammar` evidence `email_new_bridge`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157268536, length 248`. Audit `grammar` evidence `email_new_fields`.
- `Contents/MacOS/Microsoft Outlook` at `byte 166470784, length 20`. Audit `grammar` evidence `email_new_objc_stub`.
- `Contents/MacOS/Microsoft Outlook` at `byte 176041761, length 102`. Audit `grammar` evidence `email_new_selector`.
- `Contents/MacOS/Microsoft Outlook` at `byte 143402840, length 36`. Symbol `_$s13OlkCocoaUtils25EmailsCanonicalOutlookURLC14queryAccountIDSo8NSNumberCSgyF`. Audit `send` evidence `E15`.
- `Contents/MacOS/Microsoft Outlook` at `byte 143403496, length 24`. Audit `send` evidence `E16`.
- `Contents/MacOS/Microsoft Outlook` at `byte 143404152, length 20`. Audit `send` evidence `E17`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157265092, length 72`. Audit `send` evidence `E18`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157265216, length 8`. Audit `send` evidence `E19`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157265340, length 24`. Audit `send` evidence `E20`.
- `Contents/MacOS/Microsoft Outlook` at `byte 113799548, length 20`. Symbol `-[OlxRecord hash]`. Audit `send` evidence `E21`.
- `Contents/MacOS/Microsoft Outlook` at `byte 110761924, length 4`. Symbol `-[OhxAccount recordUID]`. Audit `send` evidence `E22`.
- `Contents/MacOS/Microsoft Outlook` at `byte 110762328, length 24`. Symbol `-[OhxRecordUID hash]`. Audit `send` evidence `E23`.
- `Contents/MacOS/Microsoft Outlook` at `byte 156789884, length 16`. Symbol `+[OlkUIKit accountIDKeyString]`. Audit `send` evidence `E27`.
- `Contents/Frameworks/HxCore.framework/Versions/A/HxCore` at `byte 35229652, length 88`. Symbol `-[HxObjectID hash]`. Audit `send` evidence `E35`.

```json
{
  "host": "emails",
  "path": "/open/message",
  "query_keys": [
    "account",
    "from",
    "immutableID"
  ],
  "runtime_acceptance": "untested",
  "scheme": "ms-outlook",
  "static_dispatch": "lookup_then_open_ui"
}
```

First seen `16.114.26101123`. audited arm64 constants and control flow; runtime acceptance untested.

- `Contents/MacOS/Microsoft Outlook` at `byte 157269808, length 332`. Audit `grammar` evidence `email_dispatch`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157266456, length 96`. Audit `grammar` evidence `email_open_ids`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157269884, length 136`. Audit `send` evidence `E06`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157266456, length 80`. Audit `send` evidence `E07`.
- `Contents/MacOS/Microsoft Outlook` at `byte 143387832, length 20`. Symbol `_$sSS13OlkCocoaUtilsE54reverseReplacingOccurrencesOfExchangeWebSafeCharactersSSyF`. Audit `send` evidence `E08`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157266704, length 28`. Symbol `OlkEmailsDeepLinkActionHandler.openMessage(_:) recovered name`. Audit `send` evidence `E09`.
- `Contents/MacOS/Microsoft Outlook` at `byte 133374612, length 48`. Symbol `+[OhxAccountActions retrieveMessageWithServerID:forAccount:completionBlock:]`. Audit `send` evidence `E10`.
- `Contents/MacOS/Microsoft Outlook` at `byte 133374780, length 36`. Audit `send` evidence `E11`.
- `Contents/MacOS/Microsoft Outlook` at `byte 133374892, length 20`. Audit `send` evidence `E12`.
- `Contents/MacOS/Microsoft Outlook` at `byte 133945580, length 40`. Symbol `-[OhxMessage serverId]`. Audit `send` evidence `E13`.
- `Contents/MacOS/Microsoft Outlook` at `byte 133954904, length 24`. Symbol `-[OhxMessage serverID]`. Audit `send` evidence `E14`.
- `Contents/MacOS/Microsoft Outlook` at `byte 143402840, length 36`. Symbol `_$s13OlkCocoaUtils25EmailsCanonicalOutlookURLC14queryAccountIDSo8NSNumberCSgyF`. Audit `send` evidence `E15`.
- `Contents/MacOS/Microsoft Outlook` at `byte 143403496, length 24`. Audit `send` evidence `E16`.
- `Contents/MacOS/Microsoft Outlook` at `byte 143404152, length 20`. Audit `send` evidence `E17`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157265092, length 72`. Audit `send` evidence `E18`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157265216, length 8`. Audit `send` evidence `E19`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157265340, length 24`. Audit `send` evidence `E20`.
- `Contents/MacOS/Microsoft Outlook` at `byte 113799548, length 20`. Symbol `-[OlxRecord hash]`. Audit `send` evidence `E21`.
- `Contents/MacOS/Microsoft Outlook` at `byte 110761924, length 4`. Symbol `-[OhxAccount recordUID]`. Audit `send` evidence `E22`.
- `Contents/MacOS/Microsoft Outlook` at `byte 110762328, length 24`. Symbol `-[OhxRecordUID hash]`. Audit `send` evidence `E23`.
- `Contents/MacOS/Microsoft Outlook` at `byte 156789884, length 16`. Symbol `+[OlkUIKit accountIDKeyString]`. Audit `send` evidence `E27`.
- `Contents/Frameworks/HxCore.framework/Versions/A/HxCore` at `byte 35229652, length 88`. Symbol `-[HxObjectID hash]`. Audit `send` evidence `E35`.

```json
{
  "host": "emails",
  "path": "/send",
  "query_keys": [],
  "runtime_acceptance": "untested",
  "scheme": "ms-outlook",
  "static_dispatch": "unhandled_false"
}
```

First seen `16.114.26101123`. audited arm64 constants and control flow; runtime acceptance untested.

- `Contents/MacOS/Microsoft Outlook` at `byte 157269808, length 332`. Audit `grammar` evidence `email_dispatch`.
- `Contents/MacOS/Microsoft Outlook` at `byte 143404368, length 172`. Audit `grammar` evidence `email_path_enum`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157270328, length 32`. Audit `grammar` evidence `email_send_false`.

```json
{
  "host": "events",
  "path": "/new",
  "query_keys": [
    "attendees",
    "end",
    "isallday",
    "location",
    "start",
    "title"
  ],
  "runtime_acceptance": "untested",
  "scheme": "ms-outlook",
  "static_dispatch": "parsed_forwarded"
}
```

First seen `16.114.26101123`. audited arm64 constants and control flow; runtime acceptance untested.

- `Contents/MacOS/Microsoft Outlook` at `byte 143406324, length 364`. Audit `grammar` evidence `event_keys`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157298004, length 64`. Audit `grammar` evidence `event_new_bridge`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157296828, length 264`. Audit `grammar` evidence `event_new_dates_attendees`.
- `Contents/MacOS/Microsoft Outlook` at `byte 157296336, length 168`. Audit `grammar` evidence `event_new_fields`.
- `Contents/MacOS/Microsoft Outlook` at `byte 166504768, length 20`. Audit `grammar` evidence `event_new_objc_stub`.
- `Contents/MacOS/Microsoft Outlook` at `byte 176080829, length 86`. Audit `grammar` evidence `event_new_selector`.

## url scheme

```json
{
  "CFBundleURLName": "Google OAuth Redirect",
  "CFBundleURLSchemes": [
    "com.googleusercontent.apps.445112211283-8hj5uor2tp387i59rq1caep2rr5lnkcj"
  ],
  "scheme": "com.googleusercontent.apps.445112211283-8hj5uor2tp387i59rq1caep2rr5lnkcj"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleURLTypes/2`.

```json
{
  "CFBundleURLName": "MacOutlook",
  "CFBundleURLSchemes": [
    "com.microsoft.office.outlook",
    "ms-outlook"
  ],
  "scheme": "com.microsoft.office.outlook"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleURLTypes/1`.

```json
{
  "CFBundleURLName": "Email Address URL",
  "CFBundleURLSchemes": [
    "mailto"
  ],
  "scheme": "mailto"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleURLTypes/0`.

```json
{
  "CFBundleURLName": "MacOutlook",
  "CFBundleURLSchemes": [
    "com.microsoft.office.outlook",
    "ms-outlook"
  ],
  "scheme": "ms-outlook"
}
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/CFBundleURLTypes/1`.

## user activity

```json
"INSendMessageIntent"
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/NSUserActivityTypes/3`.

```json
"INShareFocusStatusIntent"
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/NSUserActivityTypes/2`.

```json
"com.microsoft.Office.Outlook.compose.mail"
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/NSUserActivityTypes/0`.

```json
"com.microsoft.Office.Outlook.view.mail"
```

First seen `16.114.26101123`. static declaration only.

- `Contents/Info.plist` at `/NSUserActivityTypes/1`.

## Reconciliation sources

These audit revisions bind the reconstruction. Open branches remain provisional.

- PR #2040, `audits/wr443-outlook-route-survey.json` at `913029145c56534c1b6d516bd88a0d43950b5551`. State `MERGED`. Merge `913029145c56534c1b6d516bd88a0d43950b5551`.
- PR #2041, `audits/wr443-deeplink-grammar.json` at `7432eac46324f2e4db8f9e852d9402c630bfb1af`. State `OPEN`. Merge `None`.
- PR #2042, `audits/wr443-send-by-id.json` at `8f69356aac9162b1b03ffa4b3c7b33e38872cdcb`. State `OPEN`. Merge `None`.

## Extraction coverage

The atlas records hashes and sizes for 427 source files.

The extractor skipped 245 symlink paths without following them.

The JSON contains the source manifest and skipped paths. Store decoding coverage is outside this slice.
