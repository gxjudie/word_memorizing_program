# Requirements Document

## Introduction

The Japanese Vocabulary Trainer is an AI-powered application for memorizing Japanese vocabulary through contextual example sentences rather than isolated words. Learners add words to a collection, and during review the application presents each target word embedded in a freshly generated Japanese example sentence with the target word visually emphasized. Learners self-evaluate their recall across four difficulty levels, and a spaced-repetition algorithm (modeled on Anki) schedules future reviews. Because reusing the same sentence risks memorizing the sentence instead of the word, a new sentence is generated for every review. Learners can click any non-target word in a sentence to see its meaning and, for kanji, its furigana reading.

The initial implementation accepts a reasonable manual input format for adding words. Learners can add words manually or import an existing Anki deck (`.apkg` deck package or Anki plain-text export), choosing which fields hold the word, reading, and meaning in a preview before importing; Anki scheduling history is not imported. The delivery platform (native Mac application versus web application) is an open design decision and is intentionally left unspecified in these requirements.

Authentication is performed through "Sign in with Google" (Google OAuth 2.0 Authorization Code flow with PKCE and OpenID Connect, requesting only the identity scopes `openid email profile`). The backend validates the Google ID token, associates the verified issuer and subject with an application account, and creates its own session. Google sign-in establishes identity only and does not grant any Gemini model usage.

AI example-sentence generation uses the Google Gemini API, called from the backend with a Gemini API key that each Learner supplies (bring your own key). A Learner creates the key for free in Google AI Studio (https://aistudio.google.com/apikey) by following an in-app step-by-step guide. The backend stores the key per Learner, encrypted at rest, and never returns the full key to the browser; only a masked hint showing the last 4 characters is displayed. The free tier of the Gemini API is rate limited. The Gemini API terms require users to be 18 or older, and users in the European Economic Area, Switzerland, or the United Kingdom must use a paid (billing-enabled) key. The application holds no developer-owned Gemini key and involves no OpenAI or ChatGPT service. Using Gemini CLI OAuth tokens or proxying a Learner's Google AI subscription is prohibited by Google's terms, and the application does neither.

## Glossary

- **System**: The Japanese Vocabulary Trainer application as a whole.
- **Learner**: The person using the application to study Japanese vocabulary.
- **Word_Entry**: A stored vocabulary record consisting of a target Japanese word, its reading, and its meaning.
- **Target_Word**: The specific Word_Entry that a given review session is testing.
- **Example_Sentence**: An AI-generated Japanese sentence that contains the Target_Word and is used during review.
- **Sentence_Generator**: The component responsible for producing a new Example_Sentence containing the Target_Word.
- **Non_Target_Word**: Any word within an Example_Sentence other than the Target_Word.
- **Furigana**: The phonetic reading (hiragana) shown for a kanji word.
- **Difficulty_Rating**: One of four Learner-selected recall ratings for a reviewed word, ordered from very hard to very easy.
- **Scheduler**: The spaced-repetition component that computes the next review date for a Word_Entry based on the Difficulty_Rating.
- **Study_Day**: The study date to which an instant belongs, using a day boundary of 07:00 Asia/Seoul time (KST, UTC+9): the Study_Day of an instant is the KST calendar date of that instant when the KST time of day of that instant is 07:00 or later, and the KST calendar date preceding that date otherwise. The current Study_Day is the Study_Day of the current instant.
- **Review_Session**: A sequence of reviews of the capped set of Word_Entries selected when the Learner starts the session, as defined in Requirement 8 criterion 1: at most Review_Word_Limit Review_Words followed by at most New_Word_Limit New_Words.
- **Due_Word**: A Word_Entry whose next scheduled review date is on or before the current Study_Day.
- **New_Word**: A Word_Entry with no prior review, meaning that no Difficulty_Rating has ever been recorded for the Word_Entry.
- **Review_Word**: A Due_Word for which at least one Difficulty_Rating has been recorded, classified by review history so that a Word_Entry whose Scheduler state was reset after a lapse still counts as a Review_Word.
- **Study_Settings**: The single editable set of study limits stored for each Learner application account, consisting of a New_Word_Limit and a Review_Word_Limit.
- **New_Word_Limit**: The maximum number of New_Words included in one Review_Session, an integer from 0 to 1000 inclusive with a default of 50.
- **Review_Word_Limit**: The maximum number of Review_Words included in one Review_Session, an integer from 0 to 1000 inclusive with a default of 100.
- **Word_Collection**: The complete set of Word_Entries belonging to the Learner.
- **Lookup_Panel**: The UI element that displays the meaning and, where applicable, the Furigana of a clicked Non_Target_Word.
- **Google_Account**: The Learner's Google identity used to authenticate through "Sign in with Google".
- **Google_OAuth_Client_ID**: The OAuth 2.0 client identifier registered by the application with Google, used as the expected audience of a Google_ID_Token.
- **Google_ID_Token**: The OpenID Connect identity token issued by Google containing the issuer (iss), audience (aud), subject (sub), expiry (exp), nonce, email, email_verified, and name claims, signed with a key published in Google's JSON Web Key Set (JWKS).
- **Gemini_API**: The Google Gemini API endpoint used for content generation (generateContent with JSON structured output) and for key verification.
- **Gemini_API_Key**: A Google API key created by the Learner in Google AI Studio that authorizes Gemini_API requests billed to or rate limited under the Learner's own Google project.
- **Key_Hint**: The masked representation of a stored Gemini_API_Key that reveals only the last 4 characters of the key.
- **Key_Setup_Guide**: The in-app step-by-step guide that instructs the Learner how to create a Gemini_API_Key in Google AI Studio and connect the key to the System.
- **Gemini_Quota**: The rate limits and daily request limits that Google applies to a Gemini_API_Key.
- **App_Session**: The application's own authenticated session for a Learner, created after successful Google_ID_Token validation and distinct from any Google-issued token.
- **Subject_Identifier**: The combination of the verified issuer (iss) and subject (sub) claims of a Google_ID_Token that identifies the Google_Account to the application.
- **Anki_Deck_File**: A file uploaded by the Learner for import, either an Anki deck package (`.apkg`, a zip archive containing an Anki SQLite collection file) or an Anki "Export Notes as Plain Text" file (`.txt` or `.tsv`, tab-separated).
- **Anki_Note**: One note record read from an Anki_Deck_File, consisting of an ordered list of field values; in a plain-text file, one data row.
- **Note_Type**: The Anki note type of an Anki_Note in a `.apkg` file, which defines the names and order of the note's fields.
- **Import_Preview**: The pre-import view produced from an uploaded Anki_Deck_File that shows the note count, the available fields, sample Anki_Notes, and the suggested Field_Mapping, without writing any Word_Entry.
- **Field_Mapping**: The Learner's assignment of available fields to the three roles of a Word_Entry: word form (required), reading (optional, or "없음" for none), and meaning (required).
- **Field_Cleanup**: The normalization applied to each mapped field value, in this order: (1) remove every Anki sound tag of the form `[sound:...]`, (2) replace each `<br>` tag and each opening or closing `div`, `p`, or `li` tag with one space and remove every other HTML tag, (3) decode HTML character entities, (4) replace each run of Unicode whitespace characters with one space, and (5) remove leading and trailing whitespace.
- **Furigana_Notation**: Anki furigana notation of the form `base[reading]` within a field value, where base is the maximal non-empty sequence of characters immediately preceding `[` that contains no whitespace, `[`, or `]`, reading is the non-empty sequence of characters between that `[` and the next `]`, and a single space immediately preceding base, if present, is a delimiter (for example `漢字[かんじ]`, `食[た]べる`, `私[わたし]は 日本[にほん]`).
- **Import_Summary**: The result view shown after an import completes, stating the counts of created Word_Entries and of skipped Anki_Notes per skip reason, and listing skipped Anki_Notes.
- **Anki_Text_Parser**: The component that parses an Anki "Export Notes as Plain Text" file into header lines and rows of field values.
- **Anki_Text_Printer**: The component that formats rows of field values into the tab-separated Anki plain-text format accepted by the Anki_Text_Parser.

## Requirements

### Requirement 1: Add Words Manually

**User Story:** As a Learner, I want to add Japanese words to my collection using a defined input format, so that I can build a set of vocabulary to study.

#### Acceptance Criteria

1. WHEN a Learner submits a word with a Japanese word form of 1 to 50 characters, a reading of 1 to 100 characters, and a meaning of 1 to 500 characters, THE System SHALL create a Word_Entry and store it in the Word_Collection.
2. IF a Learner submits a word in which the Japanese word form, the reading, or the meaning is missing or contains only whitespace, THEN THE System SHALL reject the submission and return a validation error identifying each missing field, and SHALL NOT create a Word_Entry.
3. IF a Learner submits a word in which the Japanese word form, the reading, or the meaning exceeds its maximum length (50, 100, and 500 characters respectively), THEN THE System SHALL reject the submission and return a validation error identifying each field that exceeds its limit, and SHALL NOT create a Word_Entry.
4. IF a Learner submits a word whose Japanese word form exactly matches the Japanese word form of an existing Word_Entry in the Word_Collection, THEN THE System SHALL reject the submission, return a duplicate-entry error, and SHALL NOT modify the existing Word_Entry.
5. WHEN a Word_Entry is created, THE System SHALL set the Word_Entry next review date to the current Study_Day so that the Word_Entry is immediately a Due_Word.

### Requirement 2: Import an Anki Deck

**User Story:** As a Learner, I want to upload an existing Anki deck, preview its contents, choose which fields hold the word, reading, and meaning, and then import it, so that I can add all my words at once without manual entry.

#### Acceptance Criteria

1. THE System SHALL accept as an Anki_Deck_File an uploaded file whose extension, compared case-insensitively, is `.apkg`, `.txt`, or `.tsv`.
2. IF a Learner uploads a file whose extension, compared case-insensitively, is `.colpkg`, THEN THE System SHALL reject the upload regardless of the file size or content, SHALL create no Import_Preview and no Word_Entry, and SHALL display an unsupported-format message stating that Anki collection packages are not supported and that the Learner should export the deck from Anki as a `.apkg` deck package or as notes in plain text.
3. IF an uploaded file is larger than 50 MB (52,428,800 bytes), has an extension other than those listed in criteria 1 and 2, is unreadable or corrupted, is not a recognized Anki_Deck_File format, or contains no Anki_Note, THEN THE System SHALL abort processing, SHALL create no Import_Preview and no Word_Entry, and SHALL display an invalid-file message stating the first of these reasons that applies, in the order listed in this criterion.
4. WHEN a Learner uploads an Anki_Deck_File, THE System SHALL parse the Anki_Deck_File and present an Import_Preview, and SHALL create, modify, or delete no Word_Entry until the Learner confirms the import as defined in criterion 21.
5. WHEN the System presents an Import_Preview, THE System SHALL display the number of Anki_Notes found, the list of available fields, the suggested Field_Mapping, and the first 5 Anki_Notes in file order (or all Anki_Notes when fewer than 5 exist) with each field value shown after Field_Cleanup, where file order is data-row order for a `.txt` or `.tsv` file and ascending Anki note identifier order for a `.apkg` file.
6. WHEN a Learner uploads an Anki_Deck_File of at most 50 MB (52,428,800 bytes) that contains at most 20,000 Anki_Notes, THE System SHALL present the Import_Preview within 10 seconds of the upload completing.
7. WHEN the System reads a `.apkg` file, THE System SHALL read Anki_Notes from the collection file of the newest format present in the package, in the priority order `collection.anki21b`, then `collection.anki21`, then `collection.anki2`.
8. WHEN the System presents an Import_Preview for a `.apkg` file, THE System SHALL list each Note_Type that has at least one Anki_Note together with the Note_Type name and its Anki_Note count, SHALL select by default the Note_Type with the most Anki_Notes (choosing the Note_Type whose name comes first in Unicode code point order when counts are equal), and SHALL use the field names of the selected Note_Type, in the field order defined by that Note_Type, as the available fields.
9. WHEN the Learner selects a different Note_Type in the Import_Preview, THE System SHALL replace the available fields, the suggested Field_Mapping, and the displayed sample Anki_Notes with those of the newly selected Note_Type, and SHALL include only Anki_Notes of the selected Note_Type in the import.
10. WHEN the Anki_Text_Parser reads a `.txt` or `.tsv` file, THE Anki_Text_Parser SHALL decode the file as UTF-8 while ignoring a leading byte order mark, SHALL accept both LF and CRLF line endings, SHALL treat each line that begins with `#` and precedes the first data row as a header line excluded from the Anki_Notes, SHALL ignore empty lines, and SHALL treat each remaining row as one Anki_Note whose field values are separated by tab characters, where a field value enclosed in double quotes may contain tab characters and line breaks and two consecutive double quotes inside that value represent one double-quote character.
11. IF a `.txt` or `.tsv` file is not valid UTF-8, contains a `#separator:` header line whose value is neither `tab` (case-insensitive) nor a tab character, or contains a field value that opens with a double quote and has no closing double quote before the end of the file, THEN THE System SHALL abort processing, SHALL create no Import_Preview and no Word_Entry, and SHALL display an invalid-file message stating that only UTF-8 tab-separated Anki plain-text exports are supported.
12. WHEN a `.txt` or `.tsv` file contains a `#columns:` header line, THE System SHALL use the tab-separated column names of that header line as the names of the available fields, and SHALL name each column beyond the last named column, up to the largest number of field values in any data row, "열 N", where N is the column's 1-based position.
13. WHEN a `.txt` or `.tsv` file contains no `#columns:` header line, THE System SHALL name the available fields "열 1", "열 2", and so on, up to the largest number of field values in any data row.
14. WHEN a `.txt` or `.tsv` file contains a `#guid column:N`, `#notetype column:N`, `#deck column:N`, or `#tags column:N` header line, THE System SHALL exclude the column at 1-based position N from the available fields.
15. WHEN a data row in a `.txt` or `.tsv` file contains fewer field values than the number of columns, THE System SHALL treat each missing field value of that row as an empty value.
16. THE Anki_Text_Printer SHALL format a list of rows of field values as tab-separated lines terminated by LF, and SHALL enclose a field value in double quotes, doubling each double-quote character inside the value, when the value contains a tab character, a line break, or a double-quote character, or begins with `#`.
17. FOR ALL lists of rows in which every row has the same number of field values and contains at least one non-empty field value, parsing the output of the Anki_Text_Printer with the Anki_Text_Parser SHALL produce a list of rows equal to the original list (round-trip property).
18. WHEN the System suggests a Field_Mapping, THE System SHALL compare each available field name, case-insensitively and after removing leading and trailing whitespace, with these names and SHALL suggest the lowest-position matching field for each role: word form for `Front`, `Word`, `Expression`, `Vocab`, `Vocabulary`, `単語`, or `단어`; reading for `Reading`, `Kana`, `Furigana`, `Yomi`, `読み`, or `읽기`; meaning for `Back`, `Meaning`, `Definition`, `意味`, `뜻`, or `의미`.
19. WHEN no available field name matches a role under criterion 18, THE System SHALL evaluate the unmatched roles in the order word form, reading, meaning, and SHALL suggest for each such role the positional default field (with 3 or more available fields: field 1 for word form, field 2 for reading, field 3 for meaning; with exactly 2 available fields: field 1 for word form, "없음" for reading, field 2 for meaning; with 1 available field: field 1 for word form, "없음" for reading, and no field for meaning) when that field is not already suggested for another role, and otherwise SHALL suggest the lowest-position available field not suggested for another role for word form or meaning, "없음" for reading, or no field when no such field exists.
20. WHILE an Import_Preview is displayed, THE System SHALL let the Learner choose the word form field and the meaning field from the available fields and choose the reading field from the available fields or "없음".
21. WHEN the Learner confirms an Import_Preview with a Field_Mapping that assigns one available field to word form, a different available field to meaning, and either a third different available field or "없음" to reading, THE System SHALL run the import using that Field_Mapping.
22. IF the Learner confirms an Import_Preview with a Field_Mapping that assigns no field to word form or meaning, or assigns the same field to more than one role, THEN THE System SHALL reject the confirmation, SHALL display a mapping-error message identifying each invalid role, SHALL keep the Import_Preview displayed, and SHALL create no Word_Entry.
23. WHEN the import runs, THE System SHALL process the included Anki_Notes in the file order defined in criterion 5, and SHALL apply Field_Cleanup to each mapped field value of each included Anki_Note before resolving the reading and before applying criteria 27 through 29.
24. WHEN the cleaned word form value of an Anki_Note contains Furigana_Notation, THE System SHALL use as the word form the value in which each `base[reading]` occurrence is replaced by `base` and each delimiter space preceding a base is removed (for example `漢字[かんじ]` becomes `漢字` and `食[た]べる` becomes `食べる`), whether or not a reading field is mapped.
25. WHEN the System resolves the reading of an Anki_Note, THE System SHALL use, in this order of precedence: (a) the cleaned value of the mapped reading field when a reading field is mapped and that value is non-empty; (b) otherwise, when the cleaned word form value contains Furigana_Notation, that value with each `base[reading]` occurrence replaced by `reading` and each delimiter space preceding a base removed (for example `食[た]べる` yields `たべる`); (c) otherwise, the word form determined under criterion 24.
26. WHEN an included Anki_Note is not skipped under criteria 27 through 29, THE System SHALL create one Word_Entry in the Word_Collection from the word form of criterion 24, the reading of criterion 25, and the cleaned meaning value, and SHALL set the Word_Entry next review date to the current Study_Day so that the Word_Entry is immediately a Due_Word.
27. IF the word form determined under criterion 24 or the cleaned meaning value of an Anki_Note is empty, THEN THE System SHALL skip that Anki_Note, create no Word_Entry for that Anki_Note, and count that Anki_Note as skipped for a missing field.
28. IF an Anki_Note not skipped under criterion 27 has a word form longer than 50 characters, a reading longer than 100 characters, or a meaning longer than 500 characters, THEN THE System SHALL skip that Anki_Note without truncating any value, create no Word_Entry for that Anki_Note, and count that Anki_Note as skipped for an invalid length.
29. IF an Anki_Note not skipped under criteria 27 or 28 has a word form that exactly matches the word form of a Word_Entry existing in the Word_Collection when the import runs or of a Word_Entry created earlier in the same import, THEN THE System SHALL skip that Anki_Note, create no Word_Entry for that Anki_Note, and count that Anki_Note as skipped as a duplicate.
30. THE System SHALL import only the word form, reading, and meaning of each Anki_Note, so that each imported Word_Entry starts with no review history and the scheduling data, review logs, and media files of the Anki_Deck_File are left unimported.
31. IF a database write fails or any other error stops the import before every included Anki_Note has been processed, THEN THE System SHALL roll back every Word_Entry created by that import so that the Word_Collection is left as the Word_Collection was before the import, SHALL present no Import_Summary, and SHALL display an import-failed message stating that no words were imported.
32. WHEN the Learner confirms the import of an Anki_Deck_File containing at most 20,000 included Anki_Notes, THE System SHALL complete the import and present the Import_Summary within 30 seconds of the confirmation.
33. WHEN an import completes, THE System SHALL present an Import_Summary stating the count of created Word_Entries, the count of Anki_Notes skipped for a missing field, the count of Anki_Notes skipped as duplicates, and the count of Anki_Notes skipped for an invalid length.
34. WHEN an import completes with at least one skipped Anki_Note, THE Import_Summary SHALL list the first 20 skipped Anki_Notes in the file order defined in criterion 5, each showing the word form (or "(빈 단어)" when the word form is empty) and the skip reason.
35. WHEN the Learner cancels an Import_Preview, THE System SHALL discard the Import_Preview, SHALL delete the uploaded file content, and SHALL create no Word_Entry.
36. IF the Learner confirms an Import_Preview that was created more than 30 minutes earlier, that has already been confirmed or cancelled, or that belongs to a different application account, THEN THE System SHALL reject the confirmation, SHALL create no Word_Entry, and SHALL display a message asking the Learner to upload the Anki_Deck_File again.
37. THE System SHALL process each Anki_Deck_File on the backend for the signed-in Learner who uploaded it, SHALL associate the Import_Preview with that Learner's application account, and SHALL create imported Word_Entries only in that Learner's Word_Collection.
38. IF an upload or an import confirmation is received without an App_Session or with an App_Session that has been ended, THEN THE System SHALL deny the request as defined in Requirement 9 criterion 14, SHALL create no Import_Preview, and SHALL create no Word_Entry.
39. THE System SHALL open the collection file of a `.apkg` package read-only and SHALL read the Anki_Deck_File as data only, executing no code, script, or database extension contained in the Anki_Deck_File.
40. THE System SHALL write uploaded file content and any extracted package entries only to a temporary location dedicated to a single Import_Preview, SHALL leave media files of a `.apkg` package unextracted, and SHALL delete that temporary location when the import completes, fails, or is cancelled, or when the Import_Preview expires under criterion 36.
41. IF a `.apkg` package contains an entry whose name is an absolute path, contains a `..` path segment, or contains a backslash, or contains a collection file whose uncompressed size exceeds 200 MB, THEN THE System SHALL abort processing without extracting any entry, SHALL create no Import_Preview and no Word_Entry, and SHALL display an invalid-file message.
42. IF the Learner confirms an Import_Preview while another import for the same application account is running, THEN THE System SHALL reject the confirmation, SHALL create no Word_Entry from that Import_Preview, SHALL keep that Import_Preview unconfirmed and displayed, and SHALL display a message stating that another import is in progress.

### Requirement 3: Present a Contextual Example Sentence

**User Story:** As a Learner, I want to see a Japanese example sentence containing the target word before I see the meaning, so that I can test my recall of the word in context.

#### Acceptance Criteria

1. WHEN a Review_Session presents a Target_Word, THE System SHALL display an Example_Sentence that contains the Target_Word before revealing the Target_Word meaning.
2. WHEN an Example_Sentence is displayed, THE System SHALL render every occurrence of the Target_Word within the Example_Sentence in a visual style that differs from the surrounding sentence text in at least one observable attribute (such as color, weight, or background).
3. WHEN an Example_Sentence is displayed, THE System SHALL withhold the Target_Word meaning until the Learner activates the reveal control.
4. WHEN the Learner activates the reveal control, THE System SHALL display the Target_Word meaning while keeping the Example_Sentence visible.
5. IF no Example_Sentence containing the Target_Word is available when the Review_Session presents the Target_Word, THEN THE System SHALL present the Target_Word without an Example_Sentence and indicate to the Learner that no example sentence is available.

### Requirement 4: Generate a New Sentence Each Review

**User Story:** As a Learner, I want a different example sentence every time I review a word, so that I memorize the word rather than a single sentence.

#### Acceptance Criteria

1. WHEN a Review_Session presents a Target_Word, THE Sentence_Generator SHALL request an Example_Sentence by invoking the Gemini_API from the backend using the signed-in Learner's stored Gemini_API_Key, and SHALL make the resulting Example_Sentence available to the System for display, where the Example_Sentence contains the Target_Word either in the exact form stored in its Word_Entry (surface form) or in an inflected form whose dictionary form equals that stored form.
2. WHEN the Sentence_Generator begins a generation attempt for a Target_Word, THE Sentence_Generator SHALL complete that attempt within 15 seconds of the start of that attempt, where each generation attempt, including each additional attempt under criterion 6, is measured from its own start, so that one generation request comprises at most 2 attempts and at most 30 seconds of total attempt time.
3. WHEN a Target_Word that was shown with an Example_Sentence in a previous review is reviewed again, THE Sentence_Generator SHALL produce an Example_Sentence whose full sentence text is not identical to the text of the Example_Sentence shown in the immediately preceding review of that same Target_Word, where the comparison is made on the exact sentence text after trimming leading and trailing whitespace, whether that preceding review occurred in the current Review_Session or in an earlier Review_Session.
4. WHEN the Sentence_Generator produces an Example_Sentence for a Target_Word, THE Sentence_Generator SHALL produce an Example_Sentence whose length is between 1 and 50 words inclusive, where the Target_Word and each Non_Target_Word each count as one word and a word is one token-delimited unit as segmented for word selection in Requirement 5.
5. IF a generation attempt does not complete within 15 seconds of its start, THEN THE System SHALL abandon that attempt, SHALL NOT display any Example_Sentence returned for that attempt after the 15-second limit, SHALL display a generation-error message indicating that sentence generation failed, SHALL retain the Target_Word as an unrated item in the current Review_Session, and SHALL present a retry control.
6. IF a produced Example_Sentence does not contain the Target_Word as defined in criterion 1, has a length outside the 1 to 50 word range of criterion 4, or is identical, as defined in criterion 3, to the text of the Example_Sentence shown in the immediately preceding review of that same Target_Word, THEN THE System SHALL discard that Example_Sentence without displaying it and SHALL perform an additional generation attempt, up to a maximum of 1 additional attempt per generation request.
7. IF the signed-in Learner has no stored Gemini_API_Key when the Sentence_Generator is invoked for a Target_Word, THEN THE System SHALL NOT send any request to the Gemini_API, SHALL display a message prompting the Learner to set up a Gemini_API_Key together with a control that opens the Key_Setup_Guide, SHALL NOT produce an Example_Sentence, and SHALL retain the Target_Word as an unrated item in the current Review_Session.
8. IF the Gemini_API rejects the generation request because the Gemini_Quota of the Learner's Gemini_API_Key is exhausted (rate limit or daily limit), THEN THE System SHALL stop the generation request without performing any further attempts, SHALL display a quota message stating that the Gemini_Quota is exhausted and that the Learner should wait and retry later, SHALL NOT produce an Example_Sentence, and SHALL retain the Target_Word as an unrated item in the current Review_Session.
9. IF the Gemini_API rejects the generation request because the Learner's Gemini_API_Key is invalid or revoked, THEN THE System SHALL stop the generation request without performing any further attempts, SHALL display an invalid-key message together with a control that opens the key replacement flow defined in Requirement 10, SHALL NOT produce an Example_Sentence, and SHALL retain the Target_Word as an unrated item in the current Review_Session.
10. IF all additional generation attempts under criterion 6 produce Example_Sentences that are discarded, THEN THE System SHALL display a generation-error message indicating that sentence generation failed, SHALL retain the Target_Word as an unrated item in the current Review_Session, and SHALL present a retry control.
11. IF the Gemini_API cannot be reached or returns an error other than Gemini_Quota exhaustion or an invalid or revoked Gemini_API_Key, THEN THE System SHALL display a generation-error message indicating that sentence generation failed, SHALL NOT produce an Example_Sentence, SHALL retain the Target_Word as an unrated item in the current Review_Session, and SHALL present a retry control.
12. WHEN the Learner activates the retry control presented under criterion 5, 10, or 11, THE Sentence_Generator SHALL start a new generation request for the same Target_Word through the generation path in criterion 1, subject to criteria 2 through 9, with a new allowance of up to 1 additional attempt.

### Requirement 5: Look Up Non-Target Words

**User Story:** As a Learner, I want to click any other word in the sentence to see its meaning and reading, so that I can understand the whole sentence even when it contains words I do not know.

#### Acceptance Criteria

1. WHEN a Learner selects a Non_Target_Word in a displayed Example_Sentence, THE System SHALL display the meaning and reading of the selected Non_Target_Word in the Lookup_Panel within 1 second of the selection.
2. WHERE a selected Non_Target_Word contains kanji, THE System SHALL display the Furigana of the selected Non_Target_Word in the Lookup_Panel.
3. WHEN a Learner selects a different Non_Target_Word while the Lookup_Panel is already displaying a previous result, THE System SHALL replace the previously displayed content with the meaning and reading of the newly selected Non_Target_Word.
4. IF the meaning of a selected Non_Target_Word cannot be determined, THEN THE System SHALL display an unavailable-lookup message in the Lookup_Panel indicating that no meaning is available, and THE System SHALL leave any previously displayed lookup content cleared.
5. WHILE the Lookup_Panel is displaying lookup content for a selected Non_Target_Word, THE System SHALL continue to display that content until the Learner selects another word or dismisses the Lookup_Panel.
6. THE System SHALL display the meaning of a selected Non_Target_Word in Korean in the Lookup_Panel, while displaying the surface form and reading of the selected Non_Target_Word in Japanese.

### Requirement 6: Self-Evaluate and Rate Recall

**User Story:** As a Learner, I want to reveal the answer and rate how well I remembered the word on four levels, so that the application can schedule my next review accordingly.

#### Acceptance Criteria

1. WHEN a Learner requests to reveal the answer for a Target_Word, THE System SHALL display the reading and meaning of the Target_Word within 1 second of the request.
2. WHEN the answer for a Target_Word is revealed, THE System SHALL offer exactly four selectable Difficulty_Rating options ordered from very hard (level 1) to very easy (level 4).
3. WHILE the answer for a Target_Word has not been revealed, THE System SHALL NOT offer any Difficulty_Rating option for that Target_Word.
4. WHEN a Learner selects a Difficulty_Rating for a Target_Word, THE System SHALL record the selected Difficulty_Rating for that Target_Word and visually indicate the selected option.
5. IF a Learner selects a Difficulty_Rating for a Target_Word whose answer has not been revealed, THEN THE System SHALL reject the selection, record no Difficulty_Rating, and leave the Target_Word in its unrated state.
6. IF recording the selected Difficulty_Rating fails, THEN THE System SHALL retain the Target_Word in its unrated state and present an error indication that the rating was not saved.

### Requirement 7: Spaced-Repetition Scheduling

**User Story:** As a Learner, I want words I find hard to appear sooner and words I find easy to appear later, so that I spend review time efficiently.

#### Acceptance Criteria

1. WHEN a Learner selects a Difficulty_Rating for a Target_Word, where Difficulty_Rating is one of an ordered set of four discrete levels ranging from hardest to easiest, THE Scheduler SHALL compute a next review date for that Word_Entry from the selected Difficulty_Rating and the Word_Entry review history, producing a review interval of at least 1 day and at most 365 days.
2. WHEN a Learner selects a Difficulty_Rating for a Word_Entry that has no prior review history, THE Scheduler SHALL compute the next review date using an initial interval that depends only on the selected Difficulty_Rating.
3. THE Scheduler SHALL assign a next review interval to any Word_Entry such that, for two Difficulty_Rating levels applied to the same Word_Entry with the same review history, the interval assigned to the easier level is greater than or equal to the interval assigned to the harder level.
4. WHEN the Scheduler computes a next review date, THE System SHALL store the computed next review date on the Word_Entry.
5. IF the System cannot store the computed next review date, THEN THE System SHALL retain the Word_Entry's previous next review date and present an error indication that scheduling was not saved.

### Requirement 8: Conduct a Review Session

**User Story:** As a Learner, I want to review the words that are due today, so that I follow the spaced-repetition schedule.

#### Acceptance Criteria

1. WHEN a Learner starts a Review_Session, THE System SHALL select for the Review_Session, using the New_Word_Limit and Review_Word_Limit of that Learner's Study_Settings, first up to Review_Word_Limit Review_Words that are Due_Words for the current Study_Day, ordered by next review date ascending and then by creation order ascending, followed by up to New_Word_Limit New_Words, ordered by creation order ascending and included regardless of next review date, and SHALL leave each Review_Word and New_Word beyond either limit unselected for a later Review_Session.
2. WHILE a Review_Session has remaining unreviewed Word_Entries, THE System SHALL present exactly one Target_Word at a time for review, in the selection order defined in criterion 1.
3. WHEN a Learner records a Difficulty_Rating for a Target_Word that is not the final remaining unreviewed Word_Entry in the Review_Session, THE System SHALL present the next unreviewed Word_Entry of the Review_Session.
4. WHEN a Learner records a Difficulty_Rating for the final remaining unreviewed Word_Entry in a Review_Session, THE System SHALL end the Review_Session and display a session summary that includes the total count of Word_Entries included in the Review_Session and the count of Difficulty_Ratings recorded per rating value.
5. IF a Learner starts a Review_Session when the selection under criterion 1 contains no Word_Entry, including when no Review_Word is due for the current Study_Day and no New_Word exists, or when both the Review_Word_Limit and the New_Word_Limit are 0, THEN THE System SHALL display a message indicating that no words are due and SHALL NOT start a Review_Session.
6. IF a Learner attempts to record a Difficulty_Rating that is not one of the defined Difficulty_Rating values, THEN THE System SHALL reject the input, SHALL display a message indicating the rating is invalid, and SHALL retain the current Target_Word for re-rating.

### Requirement 9: Sign in with Google

**User Story:** As a Learner, I want to sign in with my Google account, so that the application can identify me and keep my Word_Collection and Gemini_API_Key tied to my own account without a separate password.

#### Acceptance Criteria

1. WHEN a Learner initiates login, THE System SHALL perform the OAuth 2.0 Authorization Code flow with PKCE and OpenID Connect against Google, presented to the Learner as "Sign in with Google", and SHALL include in the authorization request a state value, a nonce value, and a PKCE code challenge that are newly generated for that login attempt, not reused from any other login attempt, and valid for at most 10 minutes from the moment the authorization request is sent.
2. THE System SHALL request exactly the Google scopes `openid email profile` in the authorization request.
3. WHEN Google returns an authorization response, THE System SHALL verify, before performing any token exchange, that the returned state value matches the state value sent in an authorization request that is still pending, that was sent no more than 10 minutes earlier, and that has not already been used by an earlier authorization response.
4. IF the returned state value is missing, does not match the state value sent in a pending authorization request, belongs to an authorization request sent more than 10 minutes earlier, or has already been used by an earlier authorization response, THEN THE System SHALL reject the login, SHALL perform no token exchange, SHALL create no App_Session, and SHALL display an authentication-error message indicating the login could not be verified together with a control to retry sign-in.
5. IF Google returns an authorization error or the Learner cancels or denies authorization at Google, THEN THE System SHALL end the login attempt, SHALL create no App_Session, and SHALL display a message indicating that sign-in was not completed together with a control to retry sign-in, where activating that control starts a new login attempt as defined in criterion 1.
6. WHEN Google returns an authorization code with a state value verified as defined in criterion 3, THE System SHALL exchange the authorization code and the PKCE code verifier generated for that login attempt at the Google token endpoint exactly once, and SHALL mark that login attempt's state value as used.
7. IF the token exchange request does not return a response within 30 seconds, fails with a network error, returns an error response, or returns a response without a Google_ID_Token, THEN THE System SHALL reject the login, SHALL create no App_Session, and SHALL display an authentication-error message indicating sign-in could not be completed together with a control to retry sign-in.
8. WHEN the token exchange returns a Google_ID_Token, THE System SHALL validate, before creating an App_Session, that the issuer is `https://accounts.google.com` or `accounts.google.com`, that the audience equals the Google_OAuth_Client_ID, that the expiry time is later than the System's current time minus no more than 300 seconds of allowed clock difference, that the signature verifies against a key in Google's JWKS, that the nonce equals the nonce sent in the authorization request of the same login attempt, and that the email_verified claim is true.
9. WHEN Google_ID_Token validation succeeds, THE System SHALL associate the Subject_Identifier with an application account, creating the application account if none exists for that Subject_Identifier, SHALL use the Subject_Identifier rather than the email claim as the account key so that a later sign-in with the same Subject_Identifier resolves to the same application account even if the email claim has changed, and SHALL create its own App_Session independent of any Google-issued token that grants access only to the Word_Collection and Gemini_API_Key of that application account.
10. IF any Google_ID_Token validation check defined in criterion 8 fails, THEN THE System SHALL reject the login, SHALL create no App_Session, SHALL create or modify no application account, and SHALL display an authentication-error message indicating the account could not be verified together with a control to retry sign-in.
11. THE System SHALL NOT request any Google scope beyond `openid email profile`.
12. THE System SHALL NOT persist any Google access token or Google refresh token, and SHALL discard any such token received from the token exchange once the sign-in attempt either succeeds or is rejected.
13. WHEN a signed-in Learner logs out, THE System SHALL end the App_Session so that any later request presenting that App_Session is treated as having no App_Session, SHALL display the "Sign in with Google" entry point, and SHALL require a new sign-in before granting access to the Learner's Word_Collection or Gemini_API_Key.
14. IF a request to read or modify a Word_Collection or Gemini_API_Key is received without an App_Session or with an App_Session that has been ended, THEN THE System SHALL deny the request, SHALL return or display no data from any Word_Collection or Gemini_API_Key, and SHALL present the "Sign in with Google" entry point.

### Requirement 10: Connect a Gemini API Key

**User Story:** As a Learner, I want to connect my own free Gemini API key with clear step-by-step guidance, so that the application can generate example sentences for me without a developer-held key.

#### Acceptance Criteria

1. WHILE the signed-in Learner has no stored Gemini_API_Key, WHEN the Learner starts a Review_Session, THE System SHALL present the Key_Setup_Guide instead of starting the Review_Session.
2. WHILE the signed-in Learner has no stored Gemini_API_Key, THE System SHALL keep adding Word_Entries as defined in Requirement 1 available to the Learner.
3. THE Key_Setup_Guide SHALL present the following numbered steps in order: (1) open the Google AI Studio API key page at https://aistudio.google.com/apikey in a new browser tab, (2) sign in with a Google_Account, (3) create a key by selecting an existing Google Cloud project or creating a new project, (4) copy the full key exactly as shown in Google AI Studio, which may start with `AQ.` or with `AIza`, and (5) paste the copied key into the System's key input field.
4. THE Key_Setup_Guide SHALL state that creating a Gemini_API_Key is free and requires no payment card, that the free tier of the Gemini_API is subject to rate limits, that the Gemini_API terms require users to be 18 years of age or older, and that users in the European Economic Area, Switzerland, or the United Kingdom must use a paid (billing-enabled) Gemini_API_Key.
5. WHEN the Learner submits a Gemini_API_Key, THE System SHALL remove leading and trailing whitespace from the submitted value before validating the submitted value.
6. IF the trimmed submitted value is empty, contains any whitespace character internally, contains any non-printable or control character, is shorter than 20 characters, or is longer than 200 characters, THEN THE System SHALL reject the submission with a format message, SHALL store nothing, and SHALL send no request to the Gemini_API; otherwise THE System SHALL treat the trimmed submitted value as well-formed and proceed to verify the key, where this check is format-agnostic so that any key shape Google issues is accepted for verification (for example, Gemini_API_Keys have included both `AIza…` and `AQ.…` forms) and the decision on real validity is made by the verification request in criteria 7 through 11.
7. WHEN the trimmed submitted value is well-formed under criterion 6, THE System SHALL verify the key by sending exactly one verification request to the Gemini_API that generates no content, before storing the key, and SHALL not accept another key submission from the Learner until that verification request has completed or reached the 10-second limit in criterion 9.
8. IF the Gemini_API reports the submitted Gemini_API_Key as invalid during verification, THEN THE System SHALL reject the submission with an invalid-key message, SHALL store nothing, and SHALL leave any previously stored Gemini_API_Key unchanged.
9. IF the verification request does not return a response within 10 seconds, or fails with a transport error or a server error other than the rejections covered by criteria 8 and 10, THEN THE System SHALL display a message indicating that the key could not be verified at this time, SHALL store nothing, and SHALL leave any previously stored Gemini_API_Key unchanged.
10. IF the Gemini_API rejects the verification request because the submitted Gemini_API_Key's Gemini_Quota is exhausted, THEN THE System SHALL treat the submitted Gemini_API_Key as verified, SHALL store the Gemini_API_Key as defined in criterion 11, SHALL display the connected status that includes the Key_Hint, and SHALL display a notice indicating that the key's Gemini_Quota is currently exhausted and that Example_Sentence generation will work once the Gemini_Quota resets.
11. WHEN verification of the submitted Gemini_API_Key succeeds, THE System SHALL store the Gemini_API_Key encrypted at rest and associated with the signed-in Learner's application account, replacing any previously stored Gemini_API_Key, and SHALL display a connected status that includes the Key_Hint.
12. THE System SHALL NOT return the full stored Gemini_API_Key to the browser, and SHALL display only the Key_Hint as the browser-visible representation of a stored Gemini_API_Key.
13. THE System SHALL NOT write any Gemini_API_Key value to logs or include any Gemini_API_Key value in error messages.
14. WHEN the Learner chooses to replace the stored Gemini_API_Key, THE System SHALL apply criteria 5 through 11 to the newly submitted key, and SHALL keep the previously stored Gemini_API_Key in use until the newly submitted key is stored.
15. WHEN the Learner removes the stored Gemini_API_Key, THE System SHALL delete the stored Gemini_API_Key, SHALL display a not-connected status, and SHALL present the Key_Setup_Guide in place of any Example_Sentence generation until the Learner stores a new Gemini_API_Key through criteria 5 through 11.
16. THE Sentence_Generator SHALL invoke the Gemini_API using only the signed-in Learner's own stored Gemini_API_Key.
17. THE System SHALL NOT use any developer-held Gemini_API_Key as a fallback, and SHALL NOT use Google sign-in tokens, Gemini CLI OAuth tokens, or a Learner's Google AI subscription to invoke the Gemini_API.

### Requirement 11: Configure Study Limits

**User Story:** As a Learner, I want to set how many new words and how many review words one Review_Session includes, so that each study run fits the time I have.

#### Acceptance Criteria

1. THE System SHALL store exactly one Study_Settings for each Learner application account.
2. WHILE a Learner has not stored Study_Settings values, THE System SHALL use a New_Word_Limit of 50 and a Review_Word_Limit of 100 for that Learner.
3. WHEN a Learner opens the study settings, THE System SHALL display the current New_Word_Limit and the current Review_Word_Limit of that Learner's Study_Settings.
4. WHEN a Learner submits a New_Word_Limit and a Review_Word_Limit that are both integers from 0 to 1000 inclusive, THE System SHALL store both values in that Learner's Study_Settings and SHALL apply the stored values to each Review_Session that the Learner starts after the values are stored.
5. IF a Learner submits a New_Word_Limit or a Review_Word_Limit that is not an integer from 0 to 1000 inclusive, THEN THE System SHALL reject the submission, SHALL display a validation message identifying each invalid value, and SHALL leave both stored values of that Learner's Study_Settings unchanged.
6. WHEN a Learner starts a Review_Session while that Learner's New_Word_Limit is 0, THE System SHALL include no New_Word in that Review_Session.
7. WHEN a Learner starts a Review_Session while that Learner's Review_Word_Limit is 0, THE System SHALL include no Review_Word in that Review_Session.
8. THE System SHALL associate each Study_Settings with the signed-in Learner's application account and SHALL read and modify only the Study_Settings of the application account of the current App_Session.
9. IF a request to read or modify Study_Settings is received without an App_Session or with an App_Session that has been ended, THEN THE System SHALL deny the request as defined in Requirement 9 criterion 14, SHALL return or display no Study_Settings data, and SHALL leave every Study_Settings unchanged.
