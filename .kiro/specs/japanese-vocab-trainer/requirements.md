# Requirements Document

## Introduction

The Japanese Vocabulary Trainer is an AI-powered application for memorizing Japanese vocabulary through contextual example sentences rather than isolated words. Learners add words to a collection, and during review the application presents each target word embedded in a freshly generated Japanese example sentence with the target word visually emphasized. Learners self-evaluate their recall across four difficulty levels, and a spaced-repetition algorithm (modeled on Anki) schedules future reviews. Because reusing the same sentence risks memorizing the sentence instead of the word, a new sentence is generated for every review. Learners can click any non-target word in a sentence to see its meaning and, for kanji, its furigana reading.

The initial implementation accepts a reasonable manual input format for adding words. Bulk import of an existing Anki deck is a planned future capability and is specified as an optional feature so the data model and workflow can accommodate it later. The delivery platform (native Mac application versus web application) is an open design decision and is intentionally left unspecified in these requirements.

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
- **Review_Session**: A sequence of reviews of Word_Entries that are due on the current date.
- **Due_Word**: A Word_Entry whose scheduled next review date is on or before the current date.
- **Word_Collection**: The complete set of Word_Entries belonging to the Learner.
- **Lookup_Panel**: The UI element that displays the meaning and, where applicable, the Furigana of a clicked Non_Target_Word.

## Requirements

### Requirement 1: Add Words Manually

**User Story:** As a Learner, I want to add Japanese words to my collection using a defined input format, so that I can build a set of vocabulary to study.

#### Acceptance Criteria

1. WHEN a Learner submits a word with a Japanese word form of 1 to 50 characters, a reading of 1 to 100 characters, and a meaning of 1 to 500 characters, THE System SHALL create a Word_Entry and store it in the Word_Collection.
2. IF a Learner submits a word in which the Japanese word form, the reading, or the meaning is missing or contains only whitespace, THEN THE System SHALL reject the submission and return a validation error identifying each missing field, and SHALL NOT create a Word_Entry.
3. IF a Learner submits a word in which the Japanese word form, the reading, or the meaning exceeds its maximum length (50, 100, and 500 characters respectively), THEN THE System SHALL reject the submission and return a validation error identifying each field that exceeds its limit, and SHALL NOT create a Word_Entry.
4. IF a Learner submits a word whose Japanese word form exactly matches the Japanese word form of an existing Word_Entry in the Word_Collection, THEN THE System SHALL reject the submission, return a duplicate-entry error, and SHALL NOT modify the existing Word_Entry.
5. WHEN a Word_Entry is created, THE System SHALL set the Word_Entry next review date to the current date so that the Word_Entry is immediately a Due_Word.

### Requirement 2: Import an Anki Deck (Optional Feature)

**User Story:** As a Learner, I want to import an existing Anki deck, so that I can add all my words at once without manual entry.

#### Acceptance Criteria

1. WHERE Anki deck import is enabled, WHEN a Learner provides a well-formed Anki deck file, THE System SHALL create one Word_Entry for each record in the deck file that contains a non-empty Japanese word form.
2. WHERE Anki deck import is enabled, IF a record in the deck file is missing a Japanese word form, THEN THE System SHALL skip that record and record a missing-field skip entry in the import summary.
3. WHERE Anki deck import is enabled, IF a record in the deck file has a Japanese word form that exactly matches an existing Word_Entry or another record already imported in the same operation, THEN THE System SHALL skip that record and record a duplicate skip entry in the import summary.
4. WHERE Anki deck import is enabled, IF the provided deck file is unreadable or not a recognized Anki deck format, THEN THE System SHALL abort the import, create no Word_Entries, and present an invalid-file error.
5. WHERE Anki deck import is enabled, WHEN an import completes, THE System SHALL present an import summary stating the count of created Word_Entries, the count of records skipped for a missing field, and the count of records skipped as duplicates.

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

1. WHEN a Review_Session presents a Target_Word, THE Sentence_Generator SHALL produce an Example_Sentence that contains the Target_Word in its surface or dictionary form.
2. WHEN a Review_Session presents a Target_Word, THE Sentence_Generator SHALL produce the Example_Sentence within 5 seconds of the Target_Word being presented.
3. WHEN a Target_Word is reviewed again after a previous review, THE Sentence_Generator SHALL produce an Example_Sentence that differs from the Example_Sentence shown in the immediately preceding review of that Target_Word by at least one word other than the Target_Word.
4. WHEN a Target_Word is reviewed again after a previous review, THE Sentence_Generator SHALL produce an Example_Sentence between 1 and 50 words in length.
5. IF the Sentence_Generator does not produce an Example_Sentence within 5 seconds, THEN THE System SHALL display a generation-error message indicating that sentence generation failed, SHALL retain the Target_Word in the current Review_Session, and SHALL present a control that allows the Learner to retry generation.
6. IF the Sentence_Generator produces an Example_Sentence that does not contain the Target_Word, THEN THE System SHALL discard that Example_Sentence and SHALL retry generation up to 3 times before displaying a generation-error message to the Learner.

### Requirement 5: Look Up Non-Target Words

**User Story:** As a Learner, I want to click any other word in the sentence to see its meaning and reading, so that I can understand the whole sentence even when it contains words I do not know.

#### Acceptance Criteria

1. WHEN a Learner selects a Non_Target_Word in a displayed Example_Sentence, THE System SHALL display the meaning and reading of the selected Non_Target_Word in the Lookup_Panel within 1 second of the selection.
2. WHERE a selected Non_Target_Word contains kanji, THE System SHALL display the Furigana of the selected Non_Target_Word in the Lookup_Panel.
3. WHEN a Learner selects a different Non_Target_Word while the Lookup_Panel is already displaying a previous result, THE System SHALL replace the previously displayed content with the meaning and reading of the newly selected Non_Target_Word.
4. IF the meaning of a selected Non_Target_Word cannot be determined, THEN THE System SHALL display an unavailable-lookup message in the Lookup_Panel indicating that no meaning is available, and THE System SHALL leave any previously displayed lookup content cleared.
5. WHILE the Lookup_Panel is displaying lookup content for a selected Non_Target_Word, THE System SHALL continue to display that content until the Learner selects another word or dismisses the Lookup_Panel.

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

1. WHEN a Learner starts a Review_Session, THE System SHALL select all Word_Entries that are Due_Words for the current date, where a Due_Word is a Word_Entry whose next scheduled review date is on or before the current date.
2. WHILE a Review_Session has remaining unreviewed Due_Words, THE System SHALL present exactly one Target_Word at a time for review.
3. WHEN a Learner records a Difficulty_Rating for a Target_Word that is not the final remaining Due_Word, THE System SHALL present the next unreviewed Due_Word.
4. WHEN a Learner records a Difficulty_Rating for the final remaining Due_Word in a Review_Session, THE System SHALL end the Review_Session and display a session summary that includes the total count of Due_Words reviewed and the count of Difficulty_Ratings recorded per rating value.
5. IF a Learner starts a Review_Session when no Due_Words exist for the current date, THEN THE System SHALL display a message indicating that no words are due and SHALL NOT start a Review_Session.
6. IF a Learner attempts to record a Difficulty_Rating that is not one of the defined Difficulty_Rating values, THEN THE System SHALL reject the input, SHALL display a message indicating the rating is invalid, and SHALL retain the current Target_Word for re-rating.
