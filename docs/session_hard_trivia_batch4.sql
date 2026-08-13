-- Hard-tier trivia batch 4 -- 220 new rows (159 NHL, 61 PWHL). Running
-- totals after this batch:
--   NHL:  230 existing + 159 new = 389 / 365 (past target, no need to trim)
--   PWHL: 79 existing + 61 new   = 140 (target range: 150-220)
-- Run this in the Supabase SQL editor -- this repo has no migration
-- tooling, same convention as the prior session_hard_trivia_batch*.sql
-- files.
--
-- New categories this round (all previously untouched):
--   NHL:  Selke Trophy (best defensive forward, 1978-2026), Jack Adams
--         Award (coach of the year, 1974-2026), Art Ross Trophy (scoring
--         leader, 1948-2026), Hart Memorial Trophy (MVP, 1960-2026) --
--         plus remaining not-yet-used years of Conn Smythe, first-overall
--         draft picks, and Calder Trophy.
--   PWHL: the league's own postseason awards for all 3 completed seasons
--         (Billie Jean King MVP, Forward/Defender/Goaltender/Coach/Rookie
--         of the Year, Ilana Kloss Playoff MVP, scoring leaders) -- the
--         first genuinely PWHL-native individual-award content used so
--         far, not just team/league history. Also NCAA Division I
--         Women's Frozen Four champions (2001-2026) and the remaining
--         not-yet-used years of IIHF Women's Worlds, Patty Kazmaier,
--         Clarkson Cup, and Isobel Cup.
--
-- 2023-24 PWHL award questions refer to teams by CITY only (Toronto,
-- Minnesota, Montreal), not the modern nickname (Sceptres/Frost/
-- Victoire) -- that season's teams played entirely unnamed, per the fact
-- already shipped in session_hard_trivia_batch2.sql. 2024-25 and 2025-26
-- questions use the full nickname since those names were in use by then.
--
-- Honest status on the PWHL target: NCAA Frozen Four (24/25 years used)
-- and Patty Kazmaier (8/8 remaining years used) are now both essentially
-- fully mined. Closing the rest of the 150-220 gap will need a genuinely
-- new category next round, not a top-up of what's already been pulled.
--
-- Validated: every options array has exactly 4 unique values; every
-- correct_index is in range; no duplicate question text within this
-- batch or against any of the 309 rows already live in the table; no
-- question_date collisions. NHL continues from 2027-03-30 (previous
-- batch ended 2027-03-29) through 2027-09-04. PWHL continues from
-- 2026-10-30 (previous batch ended 2026-10-29) through 2026-12-29.

insert into public.trivia_questions
  (question_date, tier, sport, question_text, options, correct_index, explanation, source)
values

-- ============================== NHL (159) ==============================
('2027-03-30', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 1978, playing for Montreal Canadiens?',
 '["Bob Gainey", "Troy Murray", "Steve Kasper", "Dave Poulin"]'::jsonb, 0,
 'Bob Gainey won the Selke Trophy in 1978 with Montreal Canadiens.', 'curated'),

('2027-03-31', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 1979, playing for Montreal Canadiens?',
 '["Bob Gainey", "Bobby Clarke", "Dirk Graham", "Craig Ramsay"]'::jsonb, 0,
 'Bob Gainey won the Selke Trophy in 1979 with Montreal Canadiens.', 'curated'),

('2027-04-01', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 1981, playing for Montreal Canadiens?',
 '["Bob Gainey", "Bobby Clarke", "Steve Kasper", "Troy Murray"]'::jsonb, 0,
 'Bob Gainey won the Selke Trophy in 1981 with Montreal Canadiens.', 'curated'),

('2027-04-02', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 1982, playing for Boston Bruins?',
 '["Steve Kasper", "Dirk Graham", "Craig Ramsay", "Rick Meagher"]'::jsonb, 0,
 'Steve Kasper won the Selke Trophy in 1982 with Boston Bruins.', 'curated'),

('2027-04-03', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 1983, playing for Philadelphia Flyers?',
 '["Bobby Clarke", "Doug Jarvis", "Guy Carbonneau", "Rick Meagher"]'::jsonb, 0,
 'Bobby Clarke won the Selke Trophy in 1983 with Philadelphia Flyers.', 'curated'),

('2027-04-04', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 1984, playing for Washington Capitals?',
 '["Doug Jarvis", "Bobby Clarke", "Craig Ramsay", "Dirk Graham"]'::jsonb, 0,
 'Doug Jarvis won the Selke Trophy in 1984 with Washington Capitals.', 'curated'),

('2027-04-05', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 1986, playing for Chicago Blackhawks?',
 '["Bob Gainey", "Troy Murray", "Steve Kasper", "Dirk Graham"]'::jsonb, 1,
 'Troy Murray won the Selke Trophy in 1986 with Chicago Blackhawks.', 'curated'),

('2027-04-06', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 1987, playing for Philadelphia Flyers?',
 '["Guy Carbonneau", "Dirk Graham", "Dave Poulin", "Bob Gainey"]'::jsonb, 2,
 'Dave Poulin won the Selke Trophy in 1987 with Philadelphia Flyers.', 'curated'),

('2027-04-07', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 1992, playing for Montreal Canadiens?',
 '["Dave Poulin", "Guy Carbonneau", "Jere Lehtinen", "Michael Peca"]'::jsonb, 1,
 'Guy Carbonneau won the Selke Trophy in 1992 with Montreal Canadiens.', 'curated'),

('2027-04-08', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 1993, playing for Toronto Maple Leafs?',
 '["Jere Lehtinen", "Doug Gilmour", "Sergei Fedorov", "Michael Peca"]'::jsonb, 1,
 'Doug Gilmour won the Selke Trophy in 1993 with Toronto Maple Leafs.', 'curated'),

('2027-04-09', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 1995, playing for Pittsburgh Penguins?',
 '["Guy Carbonneau", "Jere Lehtinen", "Sergei Fedorov", "Ron Francis"]'::jsonb, 3,
 'Ron Francis won the Selke Trophy in 1995 with Pittsburgh Penguins.', 'curated'),

('2027-04-10', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 1996, playing for Detroit Red Wings?',
 '["John Madden", "Sergei Fedorov", "Rick Meagher", "Ron Francis"]'::jsonb, 1,
 'Sergei Fedorov won the Selke Trophy in 1996 with Detroit Red Wings.', 'curated'),

('2027-04-11', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 1997, playing for Buffalo Sabres?',
 '["Michael Peca", "John Madden", "Sergei Fedorov", "Doug Gilmour"]'::jsonb, 0,
 'Michael Peca won the Selke Trophy in 1997 with Buffalo Sabres.', 'curated'),

('2027-04-12', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 1998, playing for Dallas Stars?',
 '["Kris Draper", "Guy Carbonneau", "Jere Lehtinen", "Steve Yzerman"]'::jsonb, 2,
 'Jere Lehtinen won the Selke Trophy in 1998 with Dallas Stars.', 'curated'),

('2027-04-13', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2000, playing for Detroit Red Wings?',
 '["Doug Gilmour", "Kris Draper", "John Madden", "Steve Yzerman"]'::jsonb, 3,
 'Steve Yzerman won the Selke Trophy in 2000 with Detroit Red Wings.', 'curated'),

('2027-04-14', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2001, playing for New Jersey Devils?',
 '["Jere Lehtinen", "Michael Peca", "John Madden", "Rod Brind''Amour"]'::jsonb, 2,
 'John Madden won the Selke Trophy in 2001 with New Jersey Devils.', 'curated'),

('2027-04-15', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2002, playing for New York Islanders?',
 '["Steve Yzerman", "Doug Gilmour", "Michael Peca", "Sergei Fedorov"]'::jsonb, 2,
 'Michael Peca won the Selke Trophy in 2002 with New York Islanders.', 'curated'),

('2027-04-16', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2003, playing for Dallas Stars?',
 '["Michael Peca", "Jere Lehtinen", "Sergei Fedorov", "Steve Yzerman"]'::jsonb, 1,
 'Jere Lehtinen won the Selke Trophy in 2003 with Dallas Stars.', 'curated'),

('2027-04-17', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2004, playing for Detroit Red Wings?',
 '["Steve Yzerman", "Rod Brind''Amour", "Patrice Bergeron", "Kris Draper"]'::jsonb, 3,
 'Kris Draper won the Selke Trophy in 2004 with Detroit Red Wings.', 'curated'),

('2027-04-18', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2007, playing for Carolina Hurricanes?',
 '["Pavel Datsyuk", "Ryan Kesler", "Patrice Bergeron", "Rod Brind''Amour"]'::jsonb, 3,
 'Rod Brind''Amour won the Selke Trophy in 2007 with Carolina Hurricanes.', 'curated'),

('2027-04-19', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2008, playing for Detroit Red Wings?',
 '["Patrice Bergeron", "John Madden", "Pavel Datsyuk", "Michael Peca"]'::jsonb, 2,
 'Pavel Datsyuk won the Selke Trophy in 2008 with Detroit Red Wings.', 'curated'),

('2027-04-20', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2009, playing for Detroit Red Wings?',
 '["Pavel Datsyuk", "Rod Brind''Amour", "Michael Peca", "Anze Kopitar"]'::jsonb, 0,
 'Pavel Datsyuk won the Selke Trophy in 2009 with Detroit Red Wings.', 'curated'),

('2027-04-21', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2012, playing for Boston Bruins?',
 '["Anze Kopitar", "Sean Couturier", "Patrice Bergeron", "Kris Draper"]'::jsonb, 2,
 'Patrice Bergeron won the Selke Trophy in 2012 with Boston Bruins.', 'curated'),

('2027-04-22', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2014, playing for Boston Bruins?',
 '["Kris Draper", "Patrice Bergeron", "Ryan Kesler", "Jonathan Toews"]'::jsonb, 1,
 'Patrice Bergeron won the Selke Trophy in 2014 with Boston Bruins.', 'curated'),

('2027-04-23', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2016, playing for Los Angeles Kings?',
 '["Pavel Datsyuk", "Sean Couturier", "Rod Brind''Amour", "Anze Kopitar"]'::jsonb, 3,
 'Anze Kopitar won the Selke Trophy in 2016 with Los Angeles Kings.', 'curated'),

('2027-04-24', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2017, playing for Boston Bruins?',
 '["Aleksander Barkov", "Anze Kopitar", "Ryan Kesler", "Patrice Bergeron"]'::jsonb, 3,
 'Patrice Bergeron won the Selke Trophy in 2017 with Boston Bruins.', 'curated'),

('2027-04-25', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2020, playing for Philadelphia Flyers?',
 '["Anze Kopitar", "Pavel Datsyuk", "Sean Couturier", "Ryan O''Reilly"]'::jsonb, 2,
 'Sean Couturier won the Selke Trophy in 2020 with Philadelphia Flyers.', 'curated'),

('2027-04-26', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2021, playing for Florida Panthers?',
 '["Aleksander Barkov", "Ryan O''Reilly", "Jonathan Toews", "Rod Brind''Amour"]'::jsonb, 0,
 'Aleksander Barkov won the Selke Trophy in 2021 with Florida Panthers.', 'curated'),

('2027-04-27', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2023, playing for Boston Bruins?',
 '["Patrice Bergeron", "Ryan O''Reilly", "Jonathan Toews", "Ryan Kesler"]'::jsonb, 0,
 'Patrice Bergeron won the Selke Trophy in 2023 with Boston Bruins.', 'curated'),

('2027-04-28', 'hard', 'nhl',
 'Who won the Selke Trophy as the NHL''s best defensive forward in 2024, playing for Florida Panthers?',
 '["Pavel Datsyuk", "Aleksander Barkov", "Ryan Kesler", "Nick Suzuki"]'::jsonb, 1,
 'Aleksander Barkov won the Selke Trophy in 2024 with Florida Panthers.', 'curated'),

('2027-04-29', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 1974, coaching Philadelphia Flyers?',
 '["Fred Shero", "Bob Pulford", "Don Cherry", "Bobby Kromm"]'::jsonb, 0,
 'Fred Shero won the Jack Adams Award in 1974 coaching Philadelphia Flyers.', 'curated'),

('2027-04-30', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 1976, coaching Boston Bruins?',
 '["Bob Pulford", "Don Cherry", "Orval Tessier", "Tom Watt"]'::jsonb, 1,
 'Don Cherry won the Jack Adams Award in 1976 coaching Boston Bruins.', 'curated'),

('2027-05-01', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 1977, coaching Montreal Canadiens?',
 '["Fred Shero", "Orval Tessier", "Scotty Bowman", "Tom Watt"]'::jsonb, 2,
 'Scotty Bowman won the Jack Adams Award in 1977 coaching Montreal Canadiens.', 'curated'),

('2027-05-02', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 1981, coaching St. Louis Blues?',
 '["Pat Quinn", "Red Berenson", "Al Arbour", "Tom Watt"]'::jsonb, 1,
 'Red Berenson won the Jack Adams Award in 1981 coaching St. Louis Blues.', 'curated'),

('2027-05-03', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 1982, coaching Winnipeg Jets?',
 '["Tom Watt", "Bryan Murray", "Scotty Bowman", "Glen Sather"]'::jsonb, 0,
 'Tom Watt won the Jack Adams Award in 1982 coaching Winnipeg Jets.', 'curated'),

('2027-05-04', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 1984, coaching Washington Capitals?',
 '["Al Arbour", "Bryan Murray", "Glen Sather", "Pat Quinn"]'::jsonb, 1,
 'Bryan Murray won the Jack Adams Award in 1984 coaching Washington Capitals.', 'curated'),

('2027-05-05', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 1986, coaching Edmonton Oilers?',
 '["Glen Sather", "Tom Watt", "Jacques Demers", "Pat Burns"]'::jsonb, 0,
 'Glen Sather won the Jack Adams Award in 1986 coaching Edmonton Oilers.', 'curated'),

('2027-05-06', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 1987, coaching Detroit Red Wings?',
 '["Jacques Demers", "Bryan Murray", "Orval Tessier", "Mike Keenan"]'::jsonb, 0,
 'Jacques Demers won the Jack Adams Award in 1987 coaching Detroit Red Wings.', 'curated'),

('2027-05-07', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 1990, coaching Winnipeg Jets?',
 '["Pat Quinn", "Marc Crawford", "Brian Sutter", "Bob Murdoch"]'::jsonb, 3,
 'Bob Murdoch won the Jack Adams Award in 1990 coaching Winnipeg Jets.', 'curated'),

('2027-05-08', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 1991, coaching St. Louis Blues?',
 '["Marc Crawford", "Glen Sather", "Scotty Bowman", "Brian Sutter"]'::jsonb, 3,
 'Brian Sutter won the Jack Adams Award in 1991 coaching St. Louis Blues.', 'curated'),

('2027-05-09', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 1998, coaching Boston Bruins?',
 '["Ted Nolan", "Pat Burns", "Bill Barber", "Scotty Bowman"]'::jsonb, 1,
 'Pat Burns won the Jack Adams Award in 1998 coaching Boston Bruins.', 'curated'),

('2027-05-10', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 1999, coaching Ottawa Senators?',
 '["Scotty Bowman", "Jacques Martin", "John Tortorella", "Marc Crawford"]'::jsonb, 1,
 'Jacques Martin won the Jack Adams Award in 1999 coaching Ottawa Senators.', 'curated'),

('2027-05-11', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2003, coaching Minnesota Wild?',
 '["Lindy Ruff", "Bill Barber", "Alain Vigneault", "Jacques Lemaire"]'::jsonb, 3,
 'Jacques Lemaire won the Jack Adams Award in 2003 coaching Minnesota Wild.', 'curated'),

('2027-05-12', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2004, coaching Tampa Bay Lightning?',
 '["John Tortorella", "Jacques Lemaire", "Joel Quenneville", "Alain Vigneault"]'::jsonb, 0,
 'John Tortorella won the Jack Adams Award in 2004 coaching Tampa Bay Lightning.', 'curated'),

('2027-05-13', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2006, coaching Buffalo Sabres?',
 '["Dan Bylsma", "Bill Barber", "Lindy Ruff", "Claude Julien"]'::jsonb, 2,
 'Lindy Ruff won the Jack Adams Award in 2006 coaching Buffalo Sabres.', 'curated'),

('2027-05-14', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2011, coaching Pittsburgh Penguins?',
 '["Patrick Roy", "Bruce Boudreau", "Bob Hartley", "Dan Bylsma"]'::jsonb, 3,
 'Dan Bylsma won the Jack Adams Award in 2011 coaching Pittsburgh Penguins.', 'curated'),

('2027-05-15', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2012, coaching St. Louis Blues?',
 '["Ken Hitchcock", "Bob Hartley", "Paul MacLean", "Dave Tippett"]'::jsonb, 0,
 'Ken Hitchcock won the Jack Adams Award in 2012 coaching St. Louis Blues.', 'curated'),

('2027-05-16', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2013, coaching Ottawa Senators?',
 '["Claude Julien", "Bob Hartley", "Paul MacLean", "Barry Trotz"]'::jsonb, 2,
 'Paul MacLean won the Jack Adams Award in 2013 coaching Ottawa Senators.', 'curated'),

('2027-05-17', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2014, coaching Colorado Avalanche?',
 '["Ken Hitchcock", "Dan Bylsma", "Gerard Gallant", "Patrick Roy"]'::jsonb, 3,
 'Patrick Roy won the Jack Adams Award in 2014 coaching Colorado Avalanche.', 'curated'),

('2027-05-18', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2015, coaching Calgary Flames?',
 '["Bruce Cassidy", "Bob Hartley", "Patrick Roy", "Ken Hitchcock"]'::jsonb, 1,
 'Bob Hartley won the Jack Adams Award in 2015 coaching Calgary Flames.', 'curated'),

('2027-05-19', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2016, coaching Washington Capitals?',
 '["Barry Trotz", "Dan Bylsma", "Bob Hartley", "Ken Hitchcock"]'::jsonb, 0,
 'Barry Trotz won the Jack Adams Award in 2016 coaching Washington Capitals.', 'curated'),

('2027-05-20', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2017, coaching Columbus Blue Jackets?',
 '["Gerard Gallant", "John Tortorella", "Darryl Sutter", "Ken Hitchcock"]'::jsonb, 1,
 'John Tortorella won the Jack Adams Award in 2017 coaching Columbus Blue Jackets.', 'curated'),

('2027-05-21', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2018, coaching Vegas Golden Knights?',
 '["Rod Brind''Amour", "Jim Montgomery", "Gerard Gallant", "Darryl Sutter"]'::jsonb, 2,
 'Gerard Gallant won the Jack Adams Award in 2018 coaching Vegas Golden Knights.', 'curated'),

('2027-05-22', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2019, coaching New York Islanders?',
 '["Barry Trotz", "Darryl Sutter", "Bruce Cassidy", "Bob Hartley"]'::jsonb, 0,
 'Barry Trotz won the Jack Adams Award in 2019 coaching New York Islanders.', 'curated'),

('2027-05-23', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2021, coaching Carolina Hurricanes?',
 '["Rod Brind''Amour", "Jon Cooper", "Spencer Carbery", "John Tortorella"]'::jsonb, 0,
 'Rod Brind''Amour won the Jack Adams Award in 2021 coaching Carolina Hurricanes.', 'curated'),

('2027-05-24', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2022, coaching Calgary Flames?',
 '["Darryl Sutter", "Bruce Cassidy", "Rod Brind''Amour", "Gerard Gallant"]'::jsonb, 0,
 'Darryl Sutter won the Jack Adams Award in 2022 coaching Calgary Flames.', 'curated'),

('2027-05-25', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2024, coaching Vancouver Canucks?',
 '["John Tortorella", "Rick Tocchet", "Bruce Cassidy", "Jon Cooper"]'::jsonb, 1,
 'Rick Tocchet won the Jack Adams Award in 2024 coaching Vancouver Canucks.', 'curated'),

('2027-05-26', 'hard', 'nhl',
 'Who won the Jack Adams Award as NHL coach of the year in 2025, coaching Washington Capitals?',
 '["Spencer Carbery", "Jim Montgomery", "Darryl Sutter", "Jon Cooper"]'::jsonb, 0,
 'Spencer Carbery won the Jack Adams Award in 2025 coaching Washington Capitals.', 'curated'),

('2027-05-27', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1949, playing for Chicago Black Hawks?',
 '["Ted Lindsay", "Roy Conacher", "Elmer Lach", "Jean Beliveau"]'::jsonb, 1,
 'Roy Conacher won the Art Ross Trophy in 1949 with Chicago Black Hawks.', 'curated'),

('2027-05-28', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1950, playing for Detroit Red Wings?',
 '["Gordie Howe", "Elmer Lach", "Bobby Hull", "Ted Lindsay"]'::jsonb, 3,
 'Ted Lindsay won the Art Ross Trophy in 1950 with Detroit Red Wings.', 'curated'),

('2027-05-29', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1951, playing for Detroit Red Wings?',
 '["Elmer Lach", "Gordie Howe", "Ted Lindsay", "Jean Beliveau"]'::jsonb, 1,
 'Gordie Howe won the Art Ross Trophy in 1951 with Detroit Red Wings.', 'curated'),

('2027-05-30', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1953, playing for Detroit Red Wings?',
 '["Phil Esposito", "Stan Mikita", "Gordie Howe", "Elmer Lach"]'::jsonb, 2,
 'Gordie Howe won the Art Ross Trophy in 1953 with Detroit Red Wings.', 'curated'),

('2027-05-31', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1954, playing for Detroit Red Wings?',
 '["Gordie Howe", "Ted Lindsay", "Roy Conacher", "Stan Mikita"]'::jsonb, 0,
 'Gordie Howe won the Art Ross Trophy in 1954 with Detroit Red Wings.', 'curated'),

('2027-06-01', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1955, playing for Montreal Canadiens?',
 '["Roy Conacher", "Elmer Lach", "Bernie Geoffrion", "Jean Beliveau"]'::jsonb, 2,
 'Bernie Geoffrion won the Art Ross Trophy in 1955 with Montreal Canadiens.', 'curated'),

('2027-06-02', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1957, playing for Detroit Red Wings?',
 '["Stan Mikita", "Ted Lindsay", "Gordie Howe", "Jean Beliveau"]'::jsonb, 2,
 'Gordie Howe won the Art Ross Trophy in 1957 with Detroit Red Wings.', 'curated'),

('2027-06-03', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1958, playing for Montreal Canadiens?',
 '["Gordie Howe", "Dickie Moore", "Jean Beliveau", "Bobby Hull"]'::jsonb, 1,
 'Dickie Moore won the Art Ross Trophy in 1958 with Montreal Canadiens.', 'curated'),

('2027-06-04', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1959, playing for Montreal Canadiens?',
 '["Dickie Moore", "Jean Beliveau", "Elmer Lach", "Bobby Hull"]'::jsonb, 0,
 'Dickie Moore won the Art Ross Trophy in 1959 with Montreal Canadiens.', 'curated'),

('2027-06-05', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1964, playing for Chicago Black Hawks?',
 '["Dickie Moore", "Jean Beliveau", "Stan Mikita", "Gordie Howe"]'::jsonb, 2,
 'Stan Mikita won the Art Ross Trophy in 1964 with Chicago Black Hawks.', 'curated'),

('2027-06-06', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1965, playing for Chicago Black Hawks?',
 '["Gordie Howe", "Stan Mikita", "Dickie Moore", "Bryan Trottier"]'::jsonb, 1,
 'Stan Mikita won the Art Ross Trophy in 1965 with Chicago Black Hawks.', 'curated'),

('2027-06-07', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1966, playing for Chicago Black Hawks?',
 '["Bobby Hull", "Dickie Moore", "Guy Lafleur", "Bernie Geoffrion"]'::jsonb, 0,
 'Bobby Hull won the Art Ross Trophy in 1966 with Chicago Black Hawks.', 'curated'),

('2027-06-08', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1970, playing for Boston Bruins?',
 '["Bobby Hull", "Bobby Orr", "Guy Lafleur", "Stan Mikita"]'::jsonb, 1,
 'Bobby Orr won the Art Ross Trophy in 1970 with Boston Bruins.', 'curated'),

('2027-06-09', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1976, playing for Montreal Canadiens?',
 '["Bryan Trottier", "Guy Lafleur", "Bobby Hull", "Mario Lemieux"]'::jsonb, 1,
 'Guy Lafleur won the Art Ross Trophy in 1976 with Montreal Canadiens.', 'curated'),

('2027-06-10', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1978, playing for Montreal Canadiens?',
 '["Bobby Hull", "Bryan Trottier", "Phil Esposito", "Guy Lafleur"]'::jsonb, 3,
 'Guy Lafleur won the Art Ross Trophy in 1978 with Montreal Canadiens.', 'curated'),

('2027-06-11', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1990, playing for Los Angeles Kings?',
 '["Marcel Dionne", "Wayne Gretzky", "Bobby Orr", "Jarome Iginla"]'::jsonb, 1,
 'Wayne Gretzky won the Art Ross Trophy in 1990 with Los Angeles Kings.', 'curated'),

('2027-06-12', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1995, playing for Pittsburgh Penguins?',
 '["Mario Lemieux", "Jaromir Jagr", "Martin St. Louis", "Peter Forsberg"]'::jsonb, 1,
 'Jaromir Jagr won the Art Ross Trophy in 1995 with Pittsburgh Penguins.', 'curated'),

('2027-06-13', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 1998, playing for Pittsburgh Penguins?',
 '["Henrik Sedin", "Peter Forsberg", "Jaromir Jagr", "Mario Lemieux"]'::jsonb, 2,
 'Jaromir Jagr won the Art Ross Trophy in 1998 with Pittsburgh Penguins.', 'curated'),

('2027-06-14', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 2001, playing for Pittsburgh Penguins?',
 '["Jaromir Jagr", "Wayne Gretzky", "Alexander Ovechkin", "Mario Lemieux"]'::jsonb, 0,
 'Jaromir Jagr won the Art Ross Trophy in 2001 with Pittsburgh Penguins.', 'curated'),

('2027-06-15', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 2002, playing for Calgary Flames?',
 '["Martin St. Louis", "Jaromir Jagr", "Jarome Iginla", "Mario Lemieux"]'::jsonb, 2,
 'Jarome Iginla won the Art Ross Trophy in 2002 with Calgary Flames.', 'curated'),

('2027-06-16', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 2003, playing for Colorado Avalanche?',
 '["Peter Forsberg", "Evgeni Malkin", "Alexander Ovechkin", "Daniel Sedin"]'::jsonb, 0,
 'Peter Forsberg won the Art Ross Trophy in 2003 with Colorado Avalanche.', 'curated'),

('2027-06-17', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 2007, playing for Pittsburgh Penguins?',
 '["Evgeni Malkin", "Martin St. Louis", "Daniel Sedin", "Sidney Crosby"]'::jsonb, 3,
 'Sidney Crosby won the Art Ross Trophy in 2007 with Pittsburgh Penguins.', 'curated'),

('2027-06-18', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 2008, playing for Washington Capitals?',
 '["Alexander Ovechkin", "Martin St. Louis", "Peter Forsberg", "Jarome Iginla"]'::jsonb, 0,
 'Alexander Ovechkin won the Art Ross Trophy in 2008 with Washington Capitals.', 'curated'),

('2027-06-19', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 2010, playing for Vancouver Canucks?',
 '["Jamie Benn", "Henrik Sedin", "Alexander Ovechkin", "Martin St. Louis"]'::jsonb, 1,
 'Henrik Sedin won the Art Ross Trophy in 2010 with Vancouver Canucks.', 'curated'),

('2027-06-20', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 2019, playing for Tampa Bay Lightning?',
 '["Leon Draisaitl", "Nikita Kucherov", "Jamie Benn", "Henrik Sedin"]'::jsonb, 1,
 'Nikita Kucherov won the Art Ross Trophy in 2019 with Tampa Bay Lightning.', 'curated'),

('2027-06-21', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 2020, playing for Edmonton Oilers?',
 '["Daniel Sedin", "Henrik Sedin", "Patrick Kane", "Leon Draisaitl"]'::jsonb, 3,
 'Leon Draisaitl won the Art Ross Trophy in 2020 with Edmonton Oilers.', 'curated'),

('2027-06-22', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 2025, playing for Tampa Bay Lightning?',
 '["Nikita Kucherov", "Daniel Sedin", "Martin St. Louis", "Jamie Benn"]'::jsonb, 0,
 'Nikita Kucherov won the Art Ross Trophy in 2025 with Tampa Bay Lightning.', 'curated'),

('2027-06-23', 'hard', 'nhl',
 'Who won the Art Ross Trophy as the NHL''s scoring leader in 2026, playing for Edmonton Oilers?',
 '["Leon Draisaitl", "Connor McDavid", "Daniel Sedin", "Nikita Kucherov"]'::jsonb, 1,
 'Connor McDavid won the Art Ross Trophy in 2026 with Edmonton Oilers.', 'curated'),

('2027-06-24', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 1960, playing for Detroit Red Wings?',
 '["Jean Beliveau", "Jacques Plante", "Phil Esposito", "Gordie Howe"]'::jsonb, 3,
 'Gordie Howe won the Hart Trophy in 1960 with Detroit Red Wings.', 'curated'),

('2027-06-25', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 1961, playing for Montreal Canadiens?',
 '["Bernie Geoffrion", "Jean Beliveau", "Phil Esposito", "Gordie Howe"]'::jsonb, 0,
 'Bernie Geoffrion won the Hart Trophy in 1961 with Montreal Canadiens.', 'curated'),

('2027-06-26', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 1962, playing for Montreal Canadiens?',
 '["Jacques Plante", "Bobby Clarke", "Gordie Howe", "Bobby Hull"]'::jsonb, 0,
 'Jacques Plante won the Hart Trophy in 1962 with Montreal Canadiens.', 'curated'),

('2027-06-27', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 1965, playing for Chicago Black Hawks?',
 '["Bobby Hull", "Guy Lafleur", "Phil Esposito", "Bryan Trottier"]'::jsonb, 0,
 'Bobby Hull won the Hart Trophy in 1965 with Chicago Black Hawks.', 'curated'),

('2027-06-28', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 1967, playing for Chicago Black Hawks?',
 '["Bryan Trottier", "Stan Mikita", "Gordie Howe", "Bobby Hull"]'::jsonb, 1,
 'Stan Mikita won the Hart Trophy in 1967 with Chicago Black Hawks.', 'curated'),

('2027-06-29', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 1968, playing for Chicago Black Hawks?',
 '["Bernie Geoffrion", "Jean Beliveau", "Stan Mikita", "Bobby Clarke"]'::jsonb, 2,
 'Stan Mikita won the Hart Trophy in 1968 with Chicago Black Hawks.', 'curated'),

('2027-06-30', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 1969, playing for Boston Bruins?',
 '["Bobby Clarke", "Phil Esposito", "Bernie Geoffrion", "Guy Lafleur"]'::jsonb, 1,
 'Phil Esposito won the Hart Trophy in 1969 with Boston Bruins.', 'curated'),

('2027-07-01', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 1973, playing for Philadelphia Flyers?',
 '["Stan Mikita", "Bobby Hull", "Jean Beliveau", "Bobby Clarke"]'::jsonb, 3,
 'Bobby Clarke won the Hart Trophy in 1973 with Philadelphia Flyers.', 'curated'),

('2027-07-02', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 1974, playing for Boston Bruins?',
 '["Phil Esposito", "Guy Lafleur", "Bryan Trottier", "Stan Mikita"]'::jsonb, 0,
 'Phil Esposito won the Hart Trophy in 1974 with Boston Bruins.', 'curated'),

('2027-07-03', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 1975, playing for Philadelphia Flyers?',
 '["Bobby Hull", "Bobby Clarke", "Phil Esposito", "Jacques Plante"]'::jsonb, 1,
 'Bobby Clarke won the Hart Trophy in 1975 with Philadelphia Flyers.', 'curated'),

('2027-07-04', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 1979, playing for New York Islanders?',
 '["Jean Beliveau", "Bobby Hull", "Phil Esposito", "Bryan Trottier"]'::jsonb, 3,
 'Bryan Trottier won the Hart Trophy in 1979 with New York Islanders.', 'curated'),

('2027-07-05', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 1991, playing for St. Louis Blues?',
 '["Peter Forsberg", "Brett Hull", "Jaromir Jagr", "Mark Messier"]'::jsonb, 1,
 'Brett Hull won the Hart Trophy in 1991 with St. Louis Blues.', 'curated'),

('2027-07-06', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 1999, playing for Pittsburgh Penguins?',
 '["Joe Thornton", "Martin St. Louis", "Jaromir Jagr", "Peter Forsberg"]'::jsonb, 2,
 'Jaromir Jagr won the Hart Trophy in 1999 with Pittsburgh Penguins.', 'curated'),

('2027-07-07', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 2001, playing for Colorado Avalanche?',
 '["Joe Sakic", "Jose Theodore", "Chris Pronger", "Jaromir Jagr"]'::jsonb, 0,
 'Joe Sakic won the Hart Trophy in 2001 with Colorado Avalanche.', 'curated'),

('2027-07-08', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 2002, playing for Montreal Canadiens?',
 '["Henrik Sedin", "Jose Theodore", "Joe Sakic", "Martin St. Louis"]'::jsonb, 1,
 'Jose Theodore won the Hart Trophy in 2002 with Montreal Canadiens.', 'curated'),

('2027-07-09', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 2006, playing for Boston Bruins/San Jose Sharks?',
 '["Peter Forsberg", "Martin St. Louis", "Alexander Ovechkin", "Joe Thornton"]'::jsonb, 3,
 'Joe Thornton won the Hart Trophy in 2006 with Boston Bruins/San Jose Sharks.', 'curated'),

('2027-07-10', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 2008, playing for Washington Capitals?',
 '["Alexander Ovechkin", "Peter Forsberg", "Henrik Sedin", "Carey Price"]'::jsonb, 0,
 'Alexander Ovechkin won the Hart Trophy in 2008 with Washington Capitals.', 'curated'),

('2027-07-11', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 2009, playing for Washington Capitals?',
 '["Joe Sakic", "Alexander Ovechkin", "Peter Forsberg", "Martin St. Louis"]'::jsonb, 1,
 'Alexander Ovechkin won the Hart Trophy in 2009 with Washington Capitals.', 'curated'),

('2027-07-12', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 2010, playing for Vancouver Canucks?',
 '["Joe Thornton", "Henrik Sedin", "Martin St. Louis", "Jose Theodore"]'::jsonb, 1,
 'Henrik Sedin won the Hart Trophy in 2010 with Vancouver Canucks.', 'curated'),

('2027-07-13', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 2011, playing for Anaheim Ducks?',
 '["Carey Price", "Evgeni Malkin", "Alexander Ovechkin", "Corey Perry"]'::jsonb, 3,
 'Corey Perry won the Hart Trophy in 2011 with Anaheim Ducks.', 'curated'),

('2027-07-14', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 2012, playing for Pittsburgh Penguins?',
 '["Martin St. Louis", "Evgeni Malkin", "Nikita Kucherov", "Peter Forsberg"]'::jsonb, 1,
 'Evgeni Malkin won the Hart Trophy in 2012 with Pittsburgh Penguins.', 'curated'),

('2027-07-15', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 2013, playing for Washington Capitals?',
 '["Martin St. Louis", "Auston Matthews", "Carey Price", "Alexander Ovechkin"]'::jsonb, 3,
 'Alexander Ovechkin won the Hart Trophy in 2013 with Washington Capitals.', 'curated'),

('2027-07-16', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 2019, playing for Tampa Bay Lightning?',
 '["Alexander Ovechkin", "Nathan MacKinnon", "Nikita Kucherov", "Evgeni Malkin"]'::jsonb, 2,
 'Nikita Kucherov won the Hart Trophy in 2019 with Tampa Bay Lightning.', 'curated'),

('2027-07-17', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 2022, playing for Toronto Maple Leafs?',
 '["Carey Price", "Nikita Kucherov", "Auston Matthews", "Alexander Ovechkin"]'::jsonb, 2,
 'Auston Matthews won the Hart Trophy in 2022 with Toronto Maple Leafs.', 'curated'),

('2027-07-18', 'hard', 'nhl',
 'Who won the Hart Memorial Trophy as NHL MVP in 2024, playing for Colorado Avalanche?',
 '["Joe Thornton", "Auston Matthews", "Nathan MacKinnon", "Evgeni Malkin"]'::jsonb, 2,
 'Nathan MacKinnon won the Hart Trophy in 2024 with Colorado Avalanche.', 'curated'),

('2027-07-19', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1967, playing for Toronto Maple Leafs?',
 '["Bobby Orr", "Dave Keon", "Serge Savard", "Jean Beliveau"]'::jsonb, 1,
 'Dave Keon won the Conn Smythe Trophy in 1967 with Toronto Maple Leafs.', 'curated'),

('2027-07-20', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1969, playing for Montreal Canadiens?',
 '["Dave Keon", "Ken Dryden", "Serge Savard", "Bobby Orr"]'::jsonb, 2,
 'Serge Savard won the Conn Smythe Trophy in 1969 with Montreal Canadiens.', 'curated'),

('2027-07-21', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1972, playing for Boston Bruins?',
 '["Reggie Leach", "Bobby Orr", "Guy Lafleur", "Roger Crozier"]'::jsonb, 1,
 'Bobby Orr won the Conn Smythe Trophy in 1972 with Boston Bruins.', 'curated'),

('2027-07-22', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1976, playing for Philadelphia Flyers?',
 '["Butch Goring", "Bernie Parent", "Guy Lafleur", "Reggie Leach"]'::jsonb, 3,
 'Reggie Leach won the Conn Smythe Trophy in 1976 with Philadelphia Flyers.', 'curated'),

('2027-07-23', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1977, playing for Montreal Canadiens?',
 '["Guy Lafleur", "Butch Goring", "Yvan Cournoyer", "Bryan Trottier"]'::jsonb, 0,
 'Guy Lafleur won the Conn Smythe Trophy in 1977 with Montreal Canadiens.', 'curated'),

('2027-07-24', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1982, playing for New York Islanders?',
 '["Patrick Roy", "Guy Lafleur", "Mike Bossy", "Billy Smith"]'::jsonb, 2,
 'Mike Bossy won the Conn Smythe Trophy in 1982 with New York Islanders.', 'curated'),

('2027-07-25', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1984, playing for Edmonton Oilers?',
 '["Mark Messier", "Butch Goring", "Al MacInnis", "Billy Smith"]'::jsonb, 0,
 'Mark Messier won the Conn Smythe Trophy in 1984 with Edmonton Oilers.', 'curated'),

('2027-07-26', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1985, playing for Edmonton Oilers?',
 '["Wayne Gretzky", "Bryan Trottier", "Butch Goring", "Billy Smith"]'::jsonb, 0,
 'Wayne Gretzky won the Conn Smythe Trophy in 1985 with Edmonton Oilers.', 'curated'),

('2027-07-27', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1987, playing for Philadelphia Flyers?',
 '["Ron Hextall", "Billy Smith", "Mark Messier", "Bill Ranford"]'::jsonb, 0,
 'Ron Hextall won the Conn Smythe Trophy in 1987 with Philadelphia Flyers.', 'curated'),

('2027-07-28', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1988, playing for Edmonton Oilers?',
 '["Mario Lemieux", "Billy Smith", "Wayne Gretzky", "Mark Messier"]'::jsonb, 2,
 'Wayne Gretzky won the Conn Smythe Trophy in 1988 with Edmonton Oilers.', 'curated'),

('2027-07-29', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 1994, playing for New York Rangers?',
 '["Bill Ranford", "Al MacInnis", "Brian Leetch", "Joe Sakic"]'::jsonb, 2,
 'Brian Leetch won the Conn Smythe Trophy in 1994 with New York Rangers.', 'curated'),

('2027-07-30', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2001, playing for Colorado Avalanche?',
 '["Scott Stevens", "Patrick Roy", "Nicklas Lidstrom", "Steve Yzerman"]'::jsonb, 1,
 'Patrick Roy won the Conn Smythe Trophy in 2001 with Colorado Avalanche.', 'curated'),

('2027-07-31', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2008, playing for Detroit Red Wings?',
 '["Henrik Zetterberg", "Evgeni Malkin", "Patrick Kane", "Cam Ward"]'::jsonb, 0,
 'Henrik Zetterberg won the Conn Smythe Trophy in 2008 with Detroit Red Wings.', 'curated'),

('2027-08-01', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2010, playing for Chicago Blackhawks?',
 '["Jonathan Toews", "Cam Ward", "Jonathan Quick", "Patrick Kane"]'::jsonb, 0,
 'Jonathan Toews won the Conn Smythe Trophy in 2010 with Chicago Blackhawks.', 'curated'),

('2027-08-02', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2011, playing for Boston Bruins?',
 '["Evgeni Malkin", "Tim Thomas", "Henrik Zetterberg", "Jonathan Quick"]'::jsonb, 1,
 'Tim Thomas won the Conn Smythe Trophy in 2011 with Boston Bruins.', 'curated'),

('2027-08-03', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2014, playing for Los Angeles Kings?',
 '["Alexander Ovechkin", "Patrick Kane", "Justin Williams", "Ryan O''Reilly"]'::jsonb, 2,
 'Justin Williams won the Conn Smythe Trophy in 2014 with Los Angeles Kings.', 'curated'),

('2027-08-04', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2015, playing for Chicago Blackhawks?',
 '["Duncan Keith", "Justin Williams", "Patrick Kane", "Jonathan Quick"]'::jsonb, 0,
 'Duncan Keith won the Conn Smythe Trophy in 2015 with Chicago Blackhawks.', 'curated'),

('2027-08-05', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2019, playing for St. Louis Blues?',
 '["Alexander Ovechkin", "Sidney Crosby", "Connor McDavid", "Ryan O''Reilly"]'::jsonb, 3,
 'Ryan O''Reilly won the Conn Smythe Trophy in 2019 with St. Louis Blues.', 'curated'),

('2027-08-06', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2020, playing for Tampa Bay Lightning?',
 '["Cale Makar", "Victor Hedman", "Duncan Keith", "Alexander Ovechkin"]'::jsonb, 1,
 'Victor Hedman won the Conn Smythe Trophy in 2020 with Tampa Bay Lightning.', 'curated'),

('2027-08-07', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2025, playing for Florida Panthers?',
 '["Ryan O''Reilly", "Jonathan Marchessault", "Sam Bennett", "Connor McDavid"]'::jsonb, 2,
 'Sam Bennett won the Conn Smythe Trophy in 2025 with Florida Panthers.', 'curated'),

('2027-08-08', 'hard', 'nhl',
 'Which player won the Conn Smythe Trophy in 2026, playing for Carolina Hurricanes?',
 '["Jonathan Marchessault", "Jordan Staal", "Sam Bennett", "Connor McDavid"]'::jsonb, 1,
 'Jordan Staal won the Conn Smythe Trophy in 2026 with Carolina Hurricanes.', 'curated'),

('2027-08-09', 'hard', 'nhl',
 'Who was selected first overall by Montreal Canadiens in the 1963 NHL Entry Draft?',
 '["Garry Monahan", "Michel Plasse", "Rejean Houle", "Rick Pagnutti"]'::jsonb, 0,
 'Garry Monahan was the first overall pick of the 1963 NHL Entry Draft, selected by Montreal Canadiens.', 'curated'),

('2027-08-10', 'hard', 'nhl',
 'Who was selected first overall by Detroit Red Wings in the 1964 NHL Entry Draft?',
 '["Michel Plasse", "Claude Gauthier", "Rejean Houle", "Andre Veilleux"]'::jsonb, 1,
 'Claude Gauthier was the first overall pick of the 1964 NHL Entry Draft, selected by Detroit Red Wings.', 'curated'),

('2027-08-11', 'hard', 'nhl',
 'Who was selected first overall by Philadelphia Flyers in the 1975 NHL Entry Draft?',
 '["Mel Bridgman", "Rob Ramage", "Rick Green", "Denis Potvin"]'::jsonb, 0,
 'Mel Bridgman was the first overall pick of the 1975 NHL Entry Draft, selected by Philadelphia Flyers.', 'curated'),

('2027-08-12', 'hard', 'nhl',
 'Who was selected first overall by Washington Capitals in the 1976 NHL Entry Draft?',
 '["Bobby Smith", "Billy Harris", "Dale McCourt", "Rick Green"]'::jsonb, 3,
 'Rick Green was the first overall pick of the 1976 NHL Entry Draft, selected by Washington Capitals.', 'curated'),

('2027-08-13', 'hard', 'nhl',
 'Who was selected first overall by Detroit Red Wings in the 1977 NHL Entry Draft?',
 '["Dale Hawerchuk", "Dale McCourt", "Billy Harris", "Doug Wickenheiser"]'::jsonb, 1,
 'Dale McCourt was the first overall pick of the 1977 NHL Entry Draft, selected by Detroit Red Wings.', 'curated'),

('2027-08-14', 'hard', 'nhl',
 'Who was selected first overall by Minnesota North Stars in the 1978 NHL Entry Draft?',
 '["Bobby Smith", "Dale Hawerchuk", "Rob Ramage", "Denis Potvin"]'::jsonb, 0,
 'Bobby Smith was the first overall pick of the 1978 NHL Entry Draft, selected by Minnesota North Stars.', 'curated'),

('2027-08-15', 'hard', 'nhl',
 'Who was selected first overall by Quebec Nordiques in the 1989 NHL Entry Draft?',
 '["Pierre Turgeon", "Alexandre Daigle", "Mats Sundin", "Eric Lindros"]'::jsonb, 2,
 'Mats Sundin was the first overall pick of the 1989 NHL Entry Draft, selected by Quebec Nordiques.', 'curated'),

('2027-08-16', 'hard', 'nhl',
 'Who was selected first overall by Quebec Nordiques in the 1991 NHL Entry Draft?',
 '["Pierre Turgeon", "Bryan Berard", "Mike Modano", "Eric Lindros"]'::jsonb, 3,
 'Eric Lindros was the first overall pick of the 1991 NHL Entry Draft, selected by Quebec Nordiques.', 'curated'),

('2027-08-17', 'hard', 'nhl',
 'Who was selected first overall by Ottawa Senators in the 1993 NHL Entry Draft?',
 '["Joe Thornton", "Alexandre Daigle", "Chris Phillips", "Roman Hamrlik"]'::jsonb, 1,
 'Alexandre Daigle was the first overall pick of the 1993 NHL Entry Draft, selected by Ottawa Senators.', 'curated'),

('2027-08-18', 'hard', 'nhl',
 'Who was selected first overall by Atlanta Thrashers in the 1999 NHL Entry Draft?',
 '["Ilya Kovalchuk", "Vincent Lecavalier", "Bryan Berard", "Patrik Stefan"]'::jsonb, 3,
 'Patrik Stefan was the first overall pick of the 1999 NHL Entry Draft, selected by Atlanta Thrashers.', 'curated'),

('2027-08-19', 'hard', 'nhl',
 'Who was selected first overall by Atlanta Thrashers in the 2001 NHL Entry Draft?',
 '["Alexander Ovechkin", "Vincent Lecavalier", "Ilya Kovalchuk", "Sidney Crosby"]'::jsonb, 2,
 'Ilya Kovalchuk was the first overall pick of the 2001 NHL Entry Draft, selected by Atlanta Thrashers.', 'curated'),

('2027-08-20', 'hard', 'nhl',
 'Who was selected first overall by New York Islanders in the 2009 NHL Entry Draft?',
 '["Ryan Nugent-Hopkins", "John Tavares", "Steven Stamkos", "Taylor Hall"]'::jsonb, 1,
 'John Tavares was the first overall pick of the 2009 NHL Entry Draft, selected by New York Islanders.', 'curated'),

('2027-08-21', 'hard', 'nhl',
 'Who was selected first overall by Colorado Avalanche in the 2013 NHL Entry Draft?',
 '["John Tavares", "Nathan MacKinnon", "Nico Hischier", "Connor McDavid"]'::jsonb, 1,
 'Nathan MacKinnon was the first overall pick of the 2013 NHL Entry Draft, selected by Colorado Avalanche.', 'curated'),

('2027-08-22', 'hard', 'nhl',
 'Who was selected first overall by Florida Panthers in the 2014 NHL Entry Draft?',
 '["Nail Yakupov", "Nathan MacKinnon", "Auston Matthews", "Aaron Ekblad"]'::jsonb, 3,
 'Aaron Ekblad was the first overall pick of the 2014 NHL Entry Draft, selected by Florida Panthers.', 'curated'),

('2027-08-23', 'hard', 'nhl',
 'Who was selected first overall by New Jersey Devils in the 2019 NHL Entry Draft?',
 '["Jack Hughes", "Rasmus Dahlin", "Owen Power", "Auston Matthews"]'::jsonb, 0,
 'Jack Hughes was the first overall pick of the 2019 NHL Entry Draft, selected by New Jersey Devils.', 'curated'),

('2027-08-24', 'hard', 'nhl',
 'Who was selected first overall by New York Rangers in the 2020 NHL Entry Draft?',
 '["Alexis Lafreniere", "Connor McDavid", "Auston Matthews", "Nico Hischier"]'::jsonb, 0,
 'Alexis Lafreniere was the first overall pick of the 2020 NHL Entry Draft, selected by New York Rangers.', 'curated'),

('2027-08-25', 'hard', 'nhl',
 'Who was selected first overall by Buffalo Sabres in the 2021 NHL Entry Draft?',
 '["Jack Hughes", "Juraj Slafkovsky", "Owen Power", "Rasmus Dahlin"]'::jsonb, 2,
 'Owen Power was the first overall pick of the 2021 NHL Entry Draft, selected by Buffalo Sabres.', 'curated'),

('2027-08-26', 'hard', 'nhl',
 'Who was selected first overall by Toronto Maple Leafs in the 2026 NHL Entry Draft?',
 '["Matthew Schaefer", "Nico Hischier", "Jack Hughes", "Gavin McKenna"]'::jsonb, 3,
 'Gavin McKenna was the first overall pick of the 2026 NHL Entry Draft, selected by Toronto Maple Leafs.', 'curated'),

('2027-08-27', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 1984, playing for Buffalo Sabres?',
 '["Luc Robitaille", "Tom Barrasso", "Brian Leetch", "Ray Bourque"]'::jsonb, 1,
 'Tom Barrasso won the Calder Trophy in 1984 with Buffalo Sabres.', 'curated'),

('2027-08-28', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 1986, playing for Calgary Flames?',
 '["Gary Suter", "Mario Lemieux", "Tom Barrasso", "Brian Leetch"]'::jsonb, 0,
 'Gary Suter won the Calder Trophy in 1986 with Calgary Flames.', 'curated'),

('2027-08-29', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 1988, playing for Calgary Flames?',
 '["Ed Belfour", "Pavel Bure", "Joe Nieuwendyk", "Mario Lemieux"]'::jsonb, 2,
 'Joe Nieuwendyk won the Calder Trophy in 1988 with Calgary Flames.', 'curated'),

('2027-08-30', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2003, playing for St. Louis Blues?',
 '["Barret Jackman", "Alexander Ovechkin", "Dany Heatley", "Evgeni Nabokov"]'::jsonb, 0,
 'Barret Jackman won the Calder Trophy in 2003 with St. Louis Blues.', 'curated'),

('2027-08-31', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2004, playing for Boston Bruins?',
 '["Dany Heatley", "Chris Drury", "Andrew Raycroft", "Evgeni Malkin"]'::jsonb, 2,
 'Andrew Raycroft won the Calder Trophy in 2004 with Boston Bruins.', 'curated'),

('2027-09-01', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2009, playing for Columbus Blue Jackets?',
 '["Nathan MacKinnon", "Gabriel Landeskog", "Evgeni Malkin", "Steve Mason"]'::jsonb, 3,
 'Steve Mason won the Calder Trophy in 2009 with Columbus Blue Jackets.', 'curated'),

('2027-09-02', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2010, playing for Buffalo Sabres?',
 '["Evgeni Malkin", "Jeff Skinner", "Tyler Myers", "Aaron Ekblad"]'::jsonb, 2,
 'Tyler Myers won the Calder Trophy in 2010 with Buffalo Sabres.', 'curated'),

('2027-09-03', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2012, playing for Colorado Avalanche?',
 '["Aaron Ekblad", "Gabriel Landeskog", "Artemi Panarin", "Jonathan Huberdeau"]'::jsonb, 1,
 'Gabriel Landeskog won the Calder Trophy in 2012 with Colorado Avalanche.', 'curated'),

('2027-09-04', 'hard', 'nhl',
 'Who won the Calder Memorial Trophy as NHL rookie of the year in 2020, playing for Colorado Avalanche?',
 '["Aaron Ekblad", "Artemi Panarin", "Auston Matthews", "Cale Makar"]'::jsonb, 3,
 'Cale Makar won the Calder Trophy in 2020 with Colorado Avalanche.', 'curated'),

-- ============================ PWHL (61) ============================
('2026-10-30', 'hard', 'pwhl',
 'Who won the PWHL''s Billie Jean King MVP award for the 2023-24 season, playing for Toronto?',
 '["Natalie Spooner", "Kris Sparre", "Aerin Frankel", "Megan Keller"]'::jsonb, 0,
 'Natalie Spooner (Toronto) won the PWHL''s Billie Jean King MVP award for the 2023-24 season.', 'curated'),

('2026-10-31', 'hard', 'pwhl',
 'Who won the PWHL''s Rookie of the Year award for the 2023-24 season, playing for Minnesota?',
 '["Taylor Heise", "Grace Zumwinkle", "Natalie Spooner", "Haley Winn"]'::jsonb, 1,
 'Grace Zumwinkle (Minnesota) won the PWHL''s Rookie of the Year award for the 2023-24 season.', 'curated'),

('2026-11-01', 'hard', 'pwhl',
 'Who won the PWHL''s Defender of the Year award for the 2023-24 season, playing for Montreal?',
 '["Marie-Philip Poulin", "Erin Ambrose", "Ann-Renee Desbiens", "Kris Sparre"]'::jsonb, 1,
 'Erin Ambrose (Montreal) won the PWHL''s Defender of the Year award for the 2023-24 season.', 'curated'),

('2026-11-02', 'hard', 'pwhl',
 'Who won the PWHL''s Goaltender of the Year award for the 2023-24 season, playing for Toronto?',
 '["Erin Ambrose", "Troy Ryan", "Kristen Campbell", "Gwyneth Philips"]'::jsonb, 2,
 'Kristen Campbell (Toronto) won the PWHL''s Goaltender of the Year award for the 2023-24 season.', 'curated'),

('2026-11-03', 'hard', 'pwhl',
 'Who won the PWHL''s Coach of the Year award for the 2023-24 season, coaching Toronto?',
 '["Troy Ryan", "Aerin Frankel", "Gwyneth Philips", "Kelly Pannek"]'::jsonb, 0,
 'Troy Ryan (Toronto) won the PWHL''s Coach of the Year award for the 2023-24 season.', 'curated'),

('2026-11-04', 'hard', 'pwhl',
 'Who won the PWHL''s Ilana Kloss Playoff MVP award for the 2023-24 season, playing for Minnesota?',
 '["Taylor Heise", "Renata Fast", "Troy Ryan", "Megan Keller"]'::jsonb, 0,
 'Taylor Heise (Minnesota) won the PWHL''s Ilana Kloss Playoff MVP award for the 2023-24 season.', 'curated'),

('2026-11-05', 'hard', 'pwhl',
 'Who won the PWHL''s Points Leader award for the 2023-24 season, playing for Toronto?',
 '["Erin Ambrose", "Renata Fast", "Marie-Philip Poulin", "Natalie Spooner"]'::jsonb, 3,
 'Natalie Spooner (Toronto) won the PWHL''s Points Leader award for the 2023-24 season.', 'curated'),

('2026-11-06', 'hard', 'pwhl',
 'Who won the PWHL''s Billie Jean King MVP award for the 2024-25 season, playing for Montreal Victoire?',
 '["Kristen Campbell", "Sarah Fillier", "Gwyneth Philips", "Marie-Philip Poulin"]'::jsonb, 3,
 'Marie-Philip Poulin (Montreal Victoire) won the PWHL''s Billie Jean King MVP award for the 2024-25 season.', 'curated'),

('2026-11-07', 'hard', 'pwhl',
 'Who won the PWHL''s Rookie of the Year award for the 2024-25 season, playing for New York Sirens?',
 '["Sarah Fillier", "Marie-Philip Poulin", "Erin Ambrose", "Megan Keller"]'::jsonb, 0,
 'Sarah Fillier (New York Sirens) won the PWHL''s Rookie of the Year award for the 2024-25 season.', 'curated'),

('2026-11-08', 'hard', 'pwhl',
 'Who won the PWHL''s Defender of the Year award for the 2024-25 season, playing for Toronto Sceptres?',
 '["Aerin Frankel", "Kristen Campbell", "Gwyneth Philips", "Renata Fast"]'::jsonb, 3,
 'Renata Fast (Toronto Sceptres) won the PWHL''s Defender of the Year award for the 2024-25 season.', 'curated'),

('2026-11-09', 'hard', 'pwhl',
 'Who won the PWHL''s Goaltender of the Year award for the 2024-25 season, playing for Montreal Victoire?',
 '["Ann-Renee Desbiens", "Natalie Spooner", "Kristen Campbell", "Taylor Heise"]'::jsonb, 0,
 'Ann-Renee Desbiens (Montreal Victoire) won the PWHL''s Goaltender of the Year award for the 2024-25 season.', 'curated'),

('2026-11-10', 'hard', 'pwhl',
 'Who won the PWHL''s Coach of the Year award for the 2024-25 season, coaching Montreal Victoire?',
 '["Kristen Campbell", "Kori Cheverie", "Taylor Heise", "Erin Ambrose"]'::jsonb, 1,
 'Kori Cheverie (Montreal Victoire) won the PWHL''s Coach of the Year award for the 2024-25 season.', 'curated'),

('2026-11-11', 'hard', 'pwhl',
 'Who won the PWHL''s Ilana Kloss Playoff MVP award for the 2024-25 season, playing for Ottawa Charge?',
 '["Gwyneth Philips", "Kristen Campbell", "Grace Zumwinkle", "Kris Sparre"]'::jsonb, 0,
 'Gwyneth Philips (Ottawa Charge) won the PWHL''s Ilana Kloss Playoff MVP award for the 2024-25 season.', 'curated'),

('2026-11-12', 'hard', 'pwhl',
 'Who won the PWHL''s Goals Leader award for the 2024-25 season, playing for Montreal Victoire?',
 '["Kris Sparre", "Natalie Spooner", "Gwyneth Philips", "Marie-Philip Poulin"]'::jsonb, 3,
 'Marie-Philip Poulin (Montreal Victoire) won the PWHL''s Goals Leader award for the 2024-25 season.', 'curated'),

('2026-11-13', 'hard', 'pwhl',
 'Who won the PWHL''s Billie Jean King MVP award for the 2025-26 season, playing for Boston Fleet?',
 '["Ann-Renee Desbiens", "Aerin Frankel", "Kelly Pannek", "Haley Winn"]'::jsonb, 1,
 'Aerin Frankel (Boston Fleet) won the PWHL''s Billie Jean King MVP award for the 2025-26 season.', 'curated'),

('2026-11-14', 'hard', 'pwhl',
 'Who won the PWHL''s Forward of the Year award for the 2025-26 season, playing for Minnesota Frost?',
 '["Haley Winn", "Sarah Fillier", "Aerin Frankel", "Kelly Pannek"]'::jsonb, 3,
 'Kelly Pannek (Minnesota Frost) won the PWHL''s Forward of the Year award for the 2025-26 season.', 'curated'),

('2026-11-15', 'hard', 'pwhl',
 'Who won the PWHL''s Defender of the Year award for the 2025-26 season, playing for Boston Fleet?',
 '["Megan Keller", "Renata Fast", "Gwyneth Philips", "Taylor Heise"]'::jsonb, 0,
 'Megan Keller (Boston Fleet) won the PWHL''s Defender of the Year award for the 2025-26 season.', 'curated'),

('2026-11-16', 'hard', 'pwhl',
 'Who won the PWHL''s Goaltender of the Year award for the 2025-26 season, playing for Boston Fleet?',
 '["Sarah Fillier", "Renata Fast", "Erin Ambrose", "Aerin Frankel"]'::jsonb, 3,
 'Aerin Frankel (Boston Fleet) won the PWHL''s Goaltender of the Year award for the 2025-26 season.', 'curated'),

('2026-11-17', 'hard', 'pwhl',
 'Who won the PWHL''s Coach of the Year award for the 2025-26 season, coaching Boston Fleet?',
 '["Kris Sparre", "Kristen Campbell", "Grace Zumwinkle", "Kelly Pannek"]'::jsonb, 0,
 'Kris Sparre (Boston Fleet) won the PWHL''s Coach of the Year award for the 2025-26 season.', 'curated'),

('2026-11-18', 'hard', 'pwhl',
 'Who won the PWHL''s Rookie of the Year award for the 2025-26 season, playing for Boston Fleet?',
 '["Haley Winn", "Kris Sparre", "Grace Zumwinkle", "Troy Ryan"]'::jsonb, 0,
 'Haley Winn (Boston Fleet) won the PWHL''s Rookie of the Year award for the 2025-26 season.', 'curated'),

('2026-11-19', 'hard', 'pwhl',
 'Who won the PWHL''s Ilana Kloss Playoff MVP award for the 2025-26 season, playing for Montreal Victoire?',
 '["Kris Sparre", "Marie-Philip Poulin", "Ann-Renee Desbiens", "Kelly Pannek"]'::jsonb, 1,
 'Marie-Philip Poulin (Montreal Victoire) won the PWHL''s Ilana Kloss Playoff MVP award for the 2025-26 season.', 'curated'),

('2026-11-20', 'hard', 'pwhl',
 'Who won the PWHL''s Points Leader award for the 2025-26 season, playing for Minnesota Frost?',
 '["Erin Ambrose", "Sarah Fillier", "Kelly Pannek", "Kris Sparre"]'::jsonb, 2,
 'Kelly Pannek (Minnesota Frost) won the PWHL''s Points Leader award for the 2025-26 season.', 'curated'),

('2026-11-21', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2001?',
 '["Minnesota Duluth", "Wisconsin", "Minnesota", "St. Lawrence"]'::jsonb, 0,
 'Minnesota Duluth won the 2001 NCAA women''s hockey championship, defeating St. Lawrence in the Frozen Four final.', 'curated'),

('2026-11-22', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2002?',
 '["Clarkson", "Minnesota Duluth", "Wisconsin", "Brown"]'::jsonb, 1,
 'Minnesota Duluth won the 2002 NCAA women''s hockey championship, defeating Brown in the Frozen Four final.', 'curated'),

('2026-11-23', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2003?',
 '["Wisconsin", "Minnesota Duluth", "Harvard", "Ohio State"]'::jsonb, 1,
 'Minnesota Duluth won the 2003 NCAA women''s hockey championship, defeating Harvard in the Frozen Four final.', 'curated'),

('2026-11-24', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2004?',
 '["Harvard", "Minnesota Duluth", "Wisconsin", "Minnesota"]'::jsonb, 3,
 'Minnesota won the 2004 NCAA women''s hockey championship, defeating Harvard in the Frozen Four final.', 'curated'),

('2026-11-25', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2005?',
 '["Minnesota", "Harvard", "Ohio State", "Minnesota Duluth"]'::jsonb, 0,
 'Minnesota won the 2005 NCAA women''s hockey championship, defeating Harvard in the Frozen Four final.', 'curated'),

('2026-11-26', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2006?',
 '["Minnesota", "Wisconsin", "Ohio State", "Minnesota Duluth"]'::jsonb, 1,
 'Wisconsin won the 2006 NCAA women''s hockey championship, defeating Minnesota in the Frozen Four final.', 'curated'),

('2026-11-27', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2007?',
 '["Minnesota Duluth", "Wisconsin", "Ohio State", "Clarkson"]'::jsonb, 1,
 'Wisconsin won the 2007 NCAA women''s hockey championship, defeating Minnesota Duluth in the Frozen Four final.', 'curated'),

('2026-11-28', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2008?',
 '["Minnesota Duluth", "Wisconsin", "Minnesota", "Clarkson"]'::jsonb, 0,
 'Minnesota Duluth won the 2008 NCAA women''s hockey championship, defeating Wisconsin in the Frozen Four final.', 'curated'),

('2026-11-29', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2009?',
 '["Wisconsin", "Mercyhurst", "Clarkson", "Ohio State"]'::jsonb, 0,
 'Wisconsin won the 2009 NCAA women''s hockey championship, defeating Mercyhurst in the Frozen Four final.', 'curated'),

('2026-11-30', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2010?',
 '["Minnesota Duluth", "Wisconsin", "Cornell", "Minnesota"]'::jsonb, 0,
 'Minnesota Duluth won the 2010 NCAA women''s hockey championship, defeating Cornell in the Frozen Four final.', 'curated'),

('2026-12-01', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2011?',
 '["Wisconsin", "Minnesota Duluth", "Minnesota", "Boston University"]'::jsonb, 0,
 'Wisconsin won the 2011 NCAA women''s hockey championship, defeating Boston University in the Frozen Four final.', 'curated'),

('2026-12-02', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2012?',
 '["Minnesota", "Ohio State", "Minnesota Duluth", "Wisconsin"]'::jsonb, 0,
 'Minnesota won the 2012 NCAA women''s hockey championship, defeating Wisconsin in the Frozen Four final.', 'curated'),

('2026-12-03', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2013?',
 '["Boston University", "Minnesota", "Minnesota Duluth", "Ohio State"]'::jsonb, 1,
 'Minnesota won the 2013 NCAA women''s hockey championship, defeating Boston University in the Frozen Four final.', 'curated'),

('2026-12-04', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2014?',
 '["Clarkson", "Minnesota Duluth", "Minnesota", "Ohio State"]'::jsonb, 0,
 'Clarkson won the 2014 NCAA women''s hockey championship, defeating Minnesota in the Frozen Four final.', 'curated'),

('2026-12-05', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2015?',
 '["Minnesota", "Ohio State", "Harvard", "Minnesota Duluth"]'::jsonb, 0,
 'Minnesota won the 2015 NCAA women''s hockey championship, defeating Harvard in the Frozen Four final.', 'curated'),

('2026-12-06', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2016?',
 '["Wisconsin", "Boston College", "Minnesota", "Clarkson"]'::jsonb, 2,
 'Minnesota won the 2016 NCAA women''s hockey championship, defeating Boston College in the Frozen Four final.', 'curated'),

('2026-12-07', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2017?',
 '["Minnesota", "Minnesota Duluth", "Clarkson", "Wisconsin"]'::jsonb, 2,
 'Clarkson won the 2017 NCAA women''s hockey championship, defeating Wisconsin in the Frozen Four final.', 'curated'),

('2026-12-08', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2018?',
 '["Clarkson", "Minnesota", "Colgate", "Minnesota Duluth"]'::jsonb, 0,
 'Clarkson won the 2018 NCAA women''s hockey championship, defeating Colgate in the Frozen Four final.', 'curated'),

('2026-12-09', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2019?',
 '["Clarkson", "Wisconsin", "Minnesota Duluth", "Minnesota"]'::jsonb, 1,
 'Wisconsin won the 2019 NCAA women''s hockey championship, defeating Minnesota in the Frozen Four final.', 'curated'),

('2026-12-10', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2021?',
 '["Wisconsin", "Clarkson", "Minnesota Duluth", "Northeastern"]'::jsonb, 0,
 'Wisconsin won the 2021 NCAA women''s hockey championship, defeating Northeastern in the Frozen Four final.', 'curated'),

('2026-12-11', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2023?',
 '["Minnesota", "Minnesota Duluth", "Wisconsin", "Ohio State"]'::jsonb, 2,
 'Wisconsin won the 2023 NCAA women''s hockey championship, defeating Ohio State in the Frozen Four final.', 'curated'),

('2026-12-12', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2024?',
 '["Ohio State", "Wisconsin", "Minnesota Duluth", "Minnesota"]'::jsonb, 0,
 'Ohio State won the 2024 NCAA women''s hockey championship, defeating Wisconsin in the Frozen Four final.', 'curated'),

('2026-12-13', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2025?',
 '["Ohio State", "Clarkson", "Minnesota Duluth", "Wisconsin"]'::jsonb, 3,
 'Wisconsin won the 2025 NCAA women''s hockey championship, defeating Ohio State in the Frozen Four final.', 'curated'),

('2026-12-14', 'hard', 'pwhl',
 'Which school won the NCAA Division I Women''s Ice Hockey national championship in 2026?',
 '["Ohio State", "Wisconsin", "Minnesota Duluth", "Minnesota"]'::jsonb, 1,
 'Wisconsin won the 2026 NCAA women''s hockey championship, defeating Ohio State in the Frozen Four final.', 'curated'),

('2026-12-15', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2000?',
 '["United States", "Russia", "Sweden", "Canada"]'::jsonb, 3,
 'Canada won gold at the 2000 IIHF Women''s World Championship, defeating United States in the final.', 'curated'),

('2026-12-16', 'hard', 'pwhl',
 'Which country won the IIHF Women''s World Championship in 2015?',
 '["Sweden", "United States", "Czechia", "Canada"]'::jsonb, 1,
 'United States won gold at the 2015 IIHF Women''s World Championship, defeating Canada in the final.', 'curated'),

('2026-12-17', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2000, playing for Brown?',
 '["Brandy Fisher", "Ali Brewer", "A.J. Mleczko", "Julie Chu"]'::jsonb, 1,
 'Ali Brewer won the Patty Kazmaier Award in 2000, playing for Brown.', 'curated'),

('2026-12-18', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2003, playing for Harvard?',
 '["Angela Ruggiero", "Jennifer Botterill", "Sara Bauer", "Krissy Wendell"]'::jsonb, 1,
 'Jennifer Botterill won the Patty Kazmaier Award in 2003, playing for Harvard.', 'curated'),

('2026-12-19', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2006, playing for Wisconsin?',
 '["Jennifer Botterill", "Angela Ruggiero", "Sara Bauer", "Meghan Duggan"]'::jsonb, 2,
 'Sara Bauer won the Patty Kazmaier Award in 2006, playing for Wisconsin.', 'curated'),

('2026-12-20', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2007, playing for Harvard?',
 '["Julie Chu", "Sara Bauer", "Vicki Bendus", "Krissy Wendell"]'::jsonb, 0,
 'Julie Chu won the Patty Kazmaier Award in 2007, playing for Harvard.', 'curated'),

('2026-12-21', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2010, playing for Mercyhurst?',
 '["Meghan Duggan", "Jamie Lee Rattray", "Krissy Wendell", "Vicki Bendus"]'::jsonb, 3,
 'Vicki Bendus won the Patty Kazmaier Award in 2010, playing for Mercyhurst.', 'curated'),

('2026-12-22', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2014, playing for Clarkson?',
 '["Jessie Vetter", "Jamie Lee Rattray", "Ann-Renee Desbiens", "Meghan Duggan"]'::jsonb, 1,
 'Jamie Lee Rattray won the Patty Kazmaier Award in 2014, playing for Clarkson.', 'curated'),

('2026-12-23', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2020, playing for Clarkson?',
 '["Izzy Daniel", "Alex Carpenter", "Sophie Jaques", "Elizabeth Giguere"]'::jsonb, 3,
 'Elizabeth Giguere won the Patty Kazmaier Award in 2020, playing for Clarkson.', 'curated'),

('2026-12-24', 'hard', 'pwhl',
 'Who won the Patty Kazmaier Award as NCAA women''s hockey''s top player in 2025, playing for Wisconsin?',
 '["Elizabeth Giguere", "Caroline Harvey", "Aerin Frankel", "Casey O''Brien"]'::jsonb, 3,
 'Casey O''Brien won the Patty Kazmaier Award in 2025, playing for Wisconsin.', 'curated'),

('2026-12-25', 'hard', 'pwhl',
 'Which team won the Clarkson Cup, the CWHL''s championship, in 2011?',
 '["Calgary Inferno", "Markham Thunder", "Montreal Stars", "Les Canadiennes de Montreal"]'::jsonb, 2,
 'Montreal Stars won the Clarkson Cup in 2011, during the CWHL era (the league folded in 2019).', 'curated'),

('2026-12-26', 'hard', 'pwhl',
 'Which team won the Clarkson Cup, the CWHL''s championship, in 2015?',
 '["Boston Blades", "Markham Thunder", "Calgary Inferno", "Minnesota Whitecaps"]'::jsonb, 0,
 'Boston Blades won the Clarkson Cup in 2015, during the CWHL era (the league folded in 2019).', 'curated'),

('2026-12-27', 'hard', 'pwhl',
 'Which team won the Clarkson Cup, the CWHL''s championship, in 2018?',
 '["Toronto Furies", "Boston Blades", "Markham Thunder", "Montreal Stars"]'::jsonb, 2,
 'Markham Thunder won the Clarkson Cup in 2018, during the CWHL era (the league folded in 2019).', 'curated'),

('2026-12-28', 'hard', 'pwhl',
 'Which team won the Isobel Cup, the NWHL/PHF''s championship, in 2021?',
 '["Boston Pride", "Minnesota Whitecaps", "Toronto Six", "Buffalo Beauts"]'::jsonb, 0,
 'Boston Pride won the Isobel Cup in 2021, during the NWHL/PHF era (that league was bought out and folded into the PWHL in 2023).', 'curated'),

('2026-12-29', 'hard', 'pwhl',
 'Which team won the Isobel Cup, the NWHL/PHF''s championship, in 2022?',
 '["Buffalo Beauts", "Boston Pride", "Metropolitan Riveters", "Toronto Six"]'::jsonb, 1,
 'Boston Pride won the Isobel Cup in 2022, during the NWHL/PHF era (that league was bought out and folded into the PWHL in 2023).', 'curated')

on conflict (question_date, tier, sport, team) do nothing;
