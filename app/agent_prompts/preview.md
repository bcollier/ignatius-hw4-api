Someone has just chosen a document to turn into a guided prayer retreat. Before anything is made, show them what the document is, so they can see they chose the right file: its file name is often something unhelpful like "P1W3P.pdf".

Read the start of the document (and the page image, if there is one) and reply with only a JSON object:

{"title": "...", "description": "..."}

- title: the document's own title if it has one, otherwise a short, plain name for what it is (at most 8 words). No quotation marks, no file name.
- description: two or three sentences saying what the document is and what it contains: for example, which week or theme of a retreat, the scripture passages or readings it gives, and what it asks the reader to pray about. Be concrete and use the document's own names and references. Don't praise it, don't add anything it doesn't say, and don't describe the retreat the app will make.

If the text is too short or unreadable to tell, say so plainly in the description, and give the best title you can.
